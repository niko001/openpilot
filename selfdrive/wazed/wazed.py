#!/usr/bin/env python3
import json
import math
import time
import threading
import requests
import logging
import queue
import os
from datetime import datetime

# Set up logging first
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wazed")

# Simple function to read .env file manually instead of using python-dotenv
def read_env_file(file_path='.env'):
  env_vars = {}
  try:
    if os.path.exists(file_path):
      with open(file_path, 'r') as f:
        for line in f:
          line = line.strip()
          if line and not line.startswith('#'):
            key_value = line.split('=', 1)
            if len(key_value) == 2:
              key, value = key_value
              env_vars[key.strip()] = value.strip().strip('"\'')
      logger.info(f"Loaded {len(env_vars)} environment variables from {file_path}")
    else:
      logger.warning(f"Environment file {file_path} not found")
  except Exception as e:
    logger.error(f"Error reading environment file: {e}")

  return env_vars

# Read environment variables with correct path
ENV = read_env_file(os.path.join(os.path.dirname(__file__), '.env'))

import cereal.messaging as messaging
from cereal import log
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.selfdrived.events import Events, EventName, Alert, AlertStatus, AlertSize, Priority, VisualAlert, AudibleAlert

# Define custom Waze audible alert IDs (matching soundd.py sound_list)
WAZE_ALERT_HAZARD = 10
WAZE_ALERT_JAM = 11
WAZE_ALERT_ACCIDENT = 12
WAZE_ALERT_POLICE = 13
WAZE_ALERT_ROAD_CLOSED = 14
WAZE_ALERT_SPEED_CAMERA = 15
WAZE_ALERT_REDLIGHT_CAMERA = 16

# Constants for the Waze alerts module
WAZE_API_UPDATE_INTERVAL = 120  # Update every 2 minutes (in seconds)
BOUNDING_BOX_WIDTH = 0.07  # About 5-7 km depending on latitude
DEFAULT_ALERT_DISTANCE = 200  # Default meters, distance to trigger alert
CHECK_ALERTS_INTERVAL = 1.0  # Check for alerts every 1 second
ENABLED_CHECK_INTERVAL = 5.0  # Check if service is enabled every 5 seconds
ALERT_DURATION = 10.0  # Display alert for 10 seconds

# Add a mock alert for testing
MOCK_ALERT = {
  "type": "POLICE",
  "location": {
    "x": -117.191503,  # longitude
    "y": 32.746869     # latitude
  },
  "street": "I-5 South",
  "reportDescription": "Mock police alert for testing purposes",
  "uuid": "mock-test-alert-12345",
  "subtype": ""
}

# Global variables
alerts_cache = [MOCK_ALERT]
last_api_call_time = 0
last_alerted_uuids = set()  # To prevent showing the same alert multiple times in succession
api_is_busy = False  # Flag to prevent concurrent API calls
current_alert = None  # Currently active alert
alert_start_time = 0  # When the current alert started showing
geocoding_cache = {}  # Cache for geocoding results to avoid repeated API calls

def safe_get_bool(params, key, default=False):
  """Safely gets a boolean parameter with a default value if the key doesn't exist."""
  try:
    return params.get_bool(key)
  except Exception:
    return default

def haversine_distance(lat1, lon1, lat2, lon2):
  """Calculate the great circle distance between two points on the earth."""
  # Convert decimal degrees to radians
  lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])

  # Haversine formula
  dlon = lon2 - lon1
  dlat = lat2 - lat1
  a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
  c = 2 * math.asin(math.sqrt(a))
  r = 6371000  # Radius of Earth in meters
  return c * r

def is_approaching(car_lat, car_lon, car_bearing, alert_lat, alert_lon, threshold=None):
  # Get the user-configurable alert distance
  if threshold is None:
    # Read from params
    params = Params()
    alert_distance_str = params.get("WazeAlertsDistance")
    if alert_distance_str:
      try:
        threshold = float(alert_distance_str)
      except ValueError:
        threshold = DEFAULT_ALERT_DISTANCE
    else:
      threshold = DEFAULT_ALERT_DISTANCE

  """Determine if car is approaching the alert within the threshold distance."""
  distance = haversine_distance(car_lat, car_lon, alert_lat, alert_lon)

  # If we're already too far, return False immediately
  if distance > threshold:
    return False, distance

  # Calculate bearing to alert
  lat1, lon1, lat2, lon2 = map(math.radians, [car_lat, car_lon, alert_lat, alert_lon])
  dlon = lon2 - lon1
  y = math.sin(dlon) * math.cos(lat2)
  x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
  bearing_to_alert = math.degrees(math.atan2(y, x)) % 360

  # Check if we're heading toward the alert (within 90 degrees)
  angle_diff = min(abs(bearing_to_alert - car_bearing), abs(bearing_to_alert - car_bearing + 360),
                  abs(bearing_to_alert - car_bearing - 360))

  return angle_diff < 90 and distance <= threshold, distance

def get_street_from_google_geocoding(lat, lon, api_key, timeout=5):
  """Get street address information from Google's Reverse Geocoding API."""
  global geocoding_cache

  cache_key = f"{lat},{lon}"

  # Check if we have this result in the cache
  if cache_key in geocoding_cache:
    logger.debug(f"Wazed: Using cached street info for location lat={lat}, lon={lon}")
    return geocoding_cache[cache_key]

  try:
    # Construct the URL with parameters
    url = f"https://maps.googleapis.com/maps/api/geocode/json?latlng={lat},{lon}&key={api_key}&language=de&result_type=street_address|route"

    headers = {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0"
    }

    logger.debug(f"Wazed: Making Google Geocoding API request for location lat={lat}, lon={lon}")
    response = requests.get(url, headers=headers, timeout=timeout)

    if response.status_code == 200:
      data = response.json()
      if data.get("status") == "OK" and data.get("results") and len(data["results"]) > 0:
        # Get the first result (most relevant)
        result = data["results"][0]

        # Extract address components
        street_name = ""
        street_number = ""

        # Find road/route and street number
        for component in result.get("address_components", []):
          if "route" in component.get("types", []):
            street_name = component.get("long_name", "")
          elif "street_number" in component.get("types", []):
            street_number = component.get("long_name", "")

        # Combine for full street address
        if street_name and street_number:
          full_address = f"{street_name} {street_number}"
        else:
          full_address = street_name

        logger.debug(f"Wazed: Found street information: {full_address}")

        # Cache the result
        geocoding_cache[cache_key] = full_address

        # Keep cache size reasonable (max 500 entries)
        if len(geocoding_cache) > 500:
          # Remove a random key (simple approach to manage cache size)
          try:
            geocoding_cache.pop(next(iter(geocoding_cache)))
          except:
            # Just clear a few entries if there's an issue
            for _ in range(min(10, len(geocoding_cache))):
              try:
                geocoding_cache.pop(next(iter(geocoding_cache)))
              except:
                pass

        return full_address
      else:
        logger.debug(f"Wazed: Google Geocoding API did not return usable results: {data.get('status')}")
    else:
      logger.warning(f"Wazed: Non-200 response from Google Geocoding API: {response.status_code}")
  except Exception as e:
    logger.error(f"Wazed: Error getting street information from Google: {e}")

  # Cache empty result to avoid repeated failed lookups
  geocoding_cache[cache_key] = ""
  return ""  # Return empty string if no info found

def fetch_permanent_hazards(lat, lon, timeout=10):
  """Fetch permanent hazards like speed cameras and red light cameras from the Waze API."""
  # Get Google Maps API key from environment variables
  GOOGLE_MAPS_API_KEY = ENV.get("GOOGLE_MAPS_API_KEY", "")

  if not GOOGLE_MAPS_API_KEY:
    logger.error("Wazed: Google Maps API key not found in environment variables!")
    return []

  # Calculate bounding box
  half_width = BOUNDING_BOX_WIDTH / 2
  top = lat + half_width
  bottom = lat - half_width
  left = lon - half_width
  right = lon + half_width

  # Construct the URL for permanent hazards (fixed cameras)
  url = f"https://www.waze.com/row-Descartes/app/Features?bbox={left}%2C{bottom}%2C{right}%2C{top}&language=en-US&v=2&roadTypes=2%2C3%2C4%2C6%2C7%2C8%2C9%2C10%2C15%2C16%2C17%2C18%2C19%2C20%2C22&sandbox=true"

  permanent_hazards = []
  try:
    logger.info(f"Wazed: Making permanent hazards API request to {url}")
    headers = {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0"
    }
    response = requests.get(url, headers=headers, timeout=timeout)
    logger.info(f"Wazed: Permanent hazards response status: {response.status_code}")

    if response.status_code == 200:
      data = response.json()
      if "permanentHazards" in data and "objects" in data["permanentHazards"]:
        objects = data["permanentHazards"]["objects"]
        for idx, obj in enumerate(objects):
          # Check if it's a camera (type 10) and has subtypes
          if obj.get("type") == 10 and "subTypes" in obj and len(obj["subTypes"]) > 0:
            camera_type = obj["subTypes"][0]
            if camera_type in ["SPEED", "RED_LIGHT"]:
              # Get geometry data if available
              location = {"x": 0, "y": 0}
              if "geometry" in obj and "coordinates" in obj["geometry"]:
                coords = obj["geometry"]["coordinates"]
                location = {"x": coords[0], "y": coords[1]}  # x is longitude, y is latitude

                # Get street information from Google Maps API
                street_info = get_street_from_google_geocoding(
                  location["y"],  # latitude
                  location["x"],  # longitude
                  GOOGLE_MAPS_API_KEY
                )

              # Generate a unique ID for this camera
              uuid = f"cam-{obj.get('id', idx)}"

              # Use Google's street info if available, fall back to Waze data if not
              street = street_info if street_info else obj.get("name", "")

              # Create alert in the same format as regular alerts
              alert = {
                "type": "CAMERA",
                "subtype": camera_type,
                "location": location,
                "uuid": uuid,
                "street": street,
                "reportDescription": "",
                "isPermanent": True
              }
              permanent_hazards.append(alert)

        logger.info(f"Wazed: Processed {len(permanent_hazards)} permanent cameras from {len(objects)} objects")
      else:
        logger.warning("Wazed: No permanentHazards.objects field in API response")
    else:
      logger.warning(f"Wazed: Non-200 response from permanent hazards API: {response.status_code}")
  except Exception as e:
    logger.error(f"Wazed: Error fetching permanent hazards: {e}")

  return permanent_hazards

def fetch_waze_alerts(lat, lon, has_internet_connection=False):
  """Fetch alerts from the Waze API using the specified coordinates as the center of the bounding box."""
  global last_api_call_time, alerts_cache, api_is_busy

  current_time = time.time()

  # Only update once every WAZE_API_UPDATE_INTERVAL and ensure we're not already making an API call
  if current_time - last_api_call_time < WAZE_API_UPDATE_INTERVAL or api_is_busy:
    return alerts_cache  # Return cache even if empty (will be an empty list, not None)

  # Skip API call if there's no internet connectivity
  if not has_internet_connection:
    logger.info("Skipping Waze API call - no internet connection")
    return alerts_cache

  api_is_busy = True  # Set flag to prevent concurrent API calls

  # Initialize new alerts list with an empty list (will be populated by API calls)
  new_alerts = []

  # Calculate bounding box
  half_width = BOUNDING_BOX_WIDTH / 2
  top = lat + half_width
  bottom = lat - half_width
  left = lon - half_width
  right = lon + half_width

  # Construct the API URL for temporary alerts
  url = f"https://www.waze.com/live-map/api/georss?top={top}&bottom={bottom}&left={left}&right={right}&env=row&types=alerts"

  try:
    logger.info(f"Wazed: Making API request to {url}")
    last_api_call_time = current_time
    headers = {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0"
    }
    response = requests.get(url, headers=headers, timeout=10)
    logger.info(f"Wazed: Response status: {response.status_code}")

    if response.status_code == 200:
      response_text = response.text
      logger.info(f"Wazed: Response content: {response_text[:500]}..." if len(response_text) > 500 else response_text)

      try:
        data = response.json()
        if "alerts" in data:
          # Get temporary alerts from API
          new_alerts = data["alerts"]
          logger.info(f"Wazed: Fetched {len(new_alerts)} temporary alerts from Waze API")
        else:
          cloudlog.warning(f"Wazed: No alerts field in Waze API response. Response keys: {data.keys()}")
          logger.warning(f"No alerts field in Waze API response. Response keys: {data.keys()}")
      except json.JSONDecodeError as je:
        cloudlog.error(f"Wazed: JSON parsing error: {je}")
        logger.error(f"JSON parsing error: {je}")
    else:
      cloudlog.warning(f"Wazed: Non-200 response from API: {response.status_code}")
      logger.warning(f"Non-200 response from API: {response.status_code}")

    # Now fetch permanent hazards (cameras) and add them to the alerts
    permanent_hazards = fetch_permanent_hazards(lat, lon)
    if permanent_hazards:
      logger.info(f"Wazed: Fetched {len(permanent_hazards)} permanent hazards (cameras) from Waze API")
      new_alerts.extend(permanent_hazards)

    # If we got any alerts (temporary or permanent), update the cache
    if new_alerts:
      alerts_cache = new_alerts
      cloudlog.info(f"Wazed: Updated alerts cache with {len(alerts_cache)} total alerts")
      logger.info(f"Updated alerts cache with {len(alerts_cache)} total alerts")
  except Exception as e:
    cloudlog.error(f"Wazed: Error fetching Waze alerts: {e}")
    logger.error(f"Error fetching Waze alerts: {e}")
  finally:
    api_is_busy = False  # Clear flag regardless of success/failure

  # Make sure the mock alert is always in the cache, even if API response was empty
  if not alerts_cache:
    alerts_cache = [MOCK_ALERT]
    logger.info("No alerts from API, using only mock POLICE alert for testing")
  elif MOCK_ALERT not in alerts_cache:
    alerts_cache.append(MOCK_ALERT)
    logger.info("Added mock POLICE alert to the cache after API error")

  # Return cached data
  return alerts_cache

def get_alert_text(alert):
  """Generate alert text based on the Waze alert data."""
  alert_type = alert.get("type", "UNKNOWN")
  alert_subtype = alert.get("subtype", "")
  street = alert.get("street", "")
  description = alert.get("reportDescription", "")
  is_permanent = alert.get("isPermanent", False)

  # Format title based on alert type and subtype
  if alert_type == "CAMERA":
    if alert_subtype == "SPEED":
      title = "Speed Camera Ahead"
    elif alert_subtype == "RED_LIGHT":
      title = "Red Light Camera Ahead"
    else:
      title = "Camera Ahead"
  else:
    title_by_type = {
      "ACCIDENT": "Accident Ahead",
      "JAM": "Traffic Jam Ahead",
      "POLICE": "Police Ahead",
      "HAZARD": "Hazard Ahead",
      "ROAD_CLOSED": "Road Closed Ahead"
    }
    title = title_by_type.get(alert_type, f"{alert_type} Ahead")

  # Format the description
  text = ""
  if street:
    text += f"On {street}"

  if description and len(description) > 0:
    # Truncate description if too long
    if len(description) > 100:
      description = description[:97] + "..."
    if text:
      text += f"\n{description}"
    else:
      text = description

  # Add permanent indicator for fixed cameras
  if is_permanent and alert_type == "CAMERA":
    if text:
      text += "\nFixed Camera"
    else:
      text = "Fixed Camera"

  return title, text

def get_waze_alert_sound(alert_data):
  """Get the alert sound ID based on alert type."""
  alert_type = alert_data.get("type", "UNKNOWN")
  subtype = alert_data.get("subtype", "")

  # Map alert types to sound IDs
  if alert_type == "ACCIDENT":
    return WAZE_ALERT_ACCIDENT
  elif alert_type == "POLICE":
    return WAZE_ALERT_POLICE
  elif alert_type == "HAZARD":
    return WAZE_ALERT_HAZARD
  elif alert_type == "JAM":
    return WAZE_ALERT_JAM
  elif alert_type == "ROAD_CLOSED":
    return WAZE_ALERT_ROAD_CLOSED
  elif subtype == "SPEED":
    return WAZE_ALERT_SPEED_CAMERA
  elif subtype == "RED_LIGHT":
    return WAZE_ALERT_REDLIGHT_CAMERA
  else:
    return 0  # No sound

class WazeAlertManager:
  def __init__(self):
    self.current_lat = 0.0
    self.current_lon = 0.0
    self.bearing = 0.0
    self.pm = None
    self.events = Events()
    self.active_alert = None
    self.alert_start_time = 0

  def is_alert_type_enabled(self, alert_type, alert_subtype, params):
    """Check if the alert type is enabled in the settings"""
    # Now we can directly use get_bool since we've registered the parameters
    if alert_type == "HAZARD":
      return params.get_bool("WazeAlertsHazards")
    elif alert_type == "JAM":
      return params.get_bool("WazeAlertsJams")
    elif alert_type == "ACCIDENT":
      return params.get_bool("WazeAlertsAccidents")
    elif alert_type == "POLICE":
      return params.get_bool("WazeAlertsPolice")
    elif alert_type == "ROAD_CLOSED":
      return params.get_bool("WazeAlertsRoadClosed")
    elif alert_subtype == "SPEED_CAMERA":
      return params.get_bool("WazeAlertsSpeedCameras")
    elif alert_subtype == "REDLIGHT_CAMERA":
      return params.get_bool("WazeAlertsRedLightCameras")
    # Default to enabled for unknown types
    return True

  def publish_waze_alert_message(self, alert_data=None):
    """Publish wazeAlerts message to inform the system of Waze alerts."""
    msg = messaging.new_message('wazeAlerts')

    # Set position data
    msg.wazeAlerts.position.latitude = self.current_lat
    msg.wazeAlerts.position.longitude = self.current_lon
    msg.wazeAlerts.bearing = self.bearing

    if alert_data is not None and "alert" in alert_data:
      alert = alert_data["alert"]
      msg.wazeAlerts.alertsCount = 1
      msg.wazeAlerts.showAlert = True
      msg.wazeAlerts.alertText1 = alert["title"]
      msg.wazeAlerts.alertText2 = alert["text"]
      msg.wazeAlerts.alertType = alert["alert_type"]
      msg.wazeAlerts.alertSubType = alert.get("subtype", "")
      msg.wazeAlerts.alertSound = alert["sound_id"]
      msg.wazeAlerts.alertDistance = alert["distance"]
    else:
      msg.wazeAlerts.alertsCount = 0
      msg.wazeAlerts.showAlert = False
      msg.wazeAlerts.alertText1 = ""
      msg.wazeAlerts.alertText2 = ""
      msg.wazeAlerts.alertType = ""
      msg.wazeAlerts.alertSound = 0
      msg.wazeAlerts.alertDistance = 0

    self.pm.send('wazeAlerts', msg)

  def publish_onroad_event(self, event_name=None):
    """Publish onroadEvents message to trigger the UI alert system."""
    if event_name is not None:
      self.events.add(event_name)

    onroad_events = messaging.new_message('onroadEvents', len(self.events))
    onroad_events.onroadEvents = self.events.to_msg()
    self.pm.send('onroadEvents', onroad_events)

    # Clear events after sending - we just want to trigger the alert once
    self.events = Events()

  def check_alerts(self):
    """Check for approaching alerts and trigger UI notifications if found."""
    global current_alert, alert_start_time
    params = Params()

    # Check if Waze Alerts are enabled - handled in wazed_thread now
    # This function is only called when alerts are enabled
    current_time = time.time()

    # For debugging - log that we're checking for alerts
    logger.debug(f"Checking for alerts at lat={self.current_lat:.6f}, lon={self.current_lon:.6f}")

    # Skip if we don't have valid location data
    if self.current_lat == 0.0 and self.current_lon == 0.0:
      logger.warning("No valid GPS data, skipping alert check")
      return

    # Clear expired alert
    if self.active_alert and (current_time - self.alert_start_time > ALERT_DURATION):
      logger.info("Alert expired, clearing")
      self.active_alert = None
      self.publish_waze_alert_message()  # Send empty alert data to clear UI

    # Don't check for new alerts if we're already showing one
    if self.active_alert:
      logger.debug("Already showing an alert, skipping check")
      return

    # Check each alert in the cache
    for alert in alerts_cache:
      if 'location' not in alert or 'x' not in alert['location'] or 'y' not in alert['location']:
        continue

      alert_lat = alert['location']['y']
      alert_lon = alert['location']['x']
      alert_uuid = alert.get('uuid', '')

      # Skip alerts with invalid coordinates
      if alert_lat == 0.0 and alert_lon == 0.0:
        continue

      try:
        is_ahead, distance = is_approaching(self.current_lat, self.current_lon, self.bearing,
                                           alert_lat, alert_lon)

        # Get alert type and check if this type of alert is enabled
        alert_type = alert.get('type', 'UNKNOWN')
        alert_subtype = alert.get('subtype', '')

        # Check if this alert type is enabled by the user settings
        alert_enabled = self.is_alert_type_enabled(alert_type, alert_subtype, params)

        # If we're approaching this alert, it's enabled, and we haven't alerted about it recently
        if is_ahead and alert_enabled and alert_uuid not in last_alerted_uuids:
          # Get alert text and sound ID
          title, text = get_alert_text(alert)
          sound_id = get_waze_alert_sound(alert)

          # Create alert data
          alert_data = {
            "alert": {
              "title": title,
              "text": text,
              "distance": distance,
              "alert_type": alert.get('type', 'UNKNOWN'),
              "subtype": alert.get('subtype', ''),
              "sound_id": sound_id,
              "uuid": alert_uuid
            }
          }

          # Set current alert and start time
          self.active_alert = alert_data
          self.alert_start_time = current_time

          # Publish wazeAlerts message
          self.publish_waze_alert_message(alert_data)

          # Publish onroadEvents message to trigger UI alert
          logger.warning("About to publish onroad event for alert")
          self.publish_onroad_event(EventName.wazeAlert)
          logger.warning("Published onroad event for alert")

          # Add to alerted set to prevent repeat alerts
          last_alerted_uuids.add(alert_uuid)

          # Print alert info
          alert_message = f"{title} - {text}"
          cloudlog.warning(f"Wazed: ALERT TRIGGERED: {alert_message} - Distance: {distance:.1f}m - Type: {alert.get('type', 'UNKNOWN')}")
          logger.warning(f"ALERT TRIGGERED: {alert_message} - Distance: {distance:.1f}m - Type: {alert.get('type', 'UNKNOWN')}")

          # Clean up old UUIDs occasionally (keep max 20)
          if len(last_alerted_uuids) > 20:
            last_alerted_uuids.pop()

          # Only show one alert at a time
          return
      except Exception as e:
        # Log any errors but don't crash
        cloudlog.exception(f"Wazed: Error processing alert: {e}")
        logger.exception(f"Error processing alert: {e}")

def initialize_default_params():
  """Initialize the Waze alert parameters with default values if they don't exist."""
  params = Params()

  # Default parameter values
  defaults = {
    "WazeAlertsEnabled": True,
    "WazeAlertsHazards": True,
    "WazeAlertsJams": True,
    "WazeAlertsAccidents": True,
    "WazeAlertsPolice": True,
    "WazeAlertsRoadClosed": True,
    "WazeAlertsSpeedCameras": True,
    "WazeAlertsRedLightCameras": True,
    "RunWazed": True,
  }

  # Initialize boolean parameters
  for key, default_value in defaults.items():
    # Check if the parameter exists by directly checking if the raw value is empty
    param_value = params.get(key)
    if param_value in (None, b''):
      logger.info(f"Creating parameter {key} with default value {default_value}")
      params.put_bool(key, default_value)
    else:
      # Log the existing value for debugging
      logger.info(f"Parameter {key} already exists with value: {param_value == b'1'}")

  # Initialize distance parameter (string parameter)
  if params.get("WazeAlertsDistance") in (None, b''):
    logger.info("Creating WazeAlertsDistance parameter with default value 200")
    params.put("WazeAlertsDistance", "200")

def has_internet(sm):
  """Check if the device has internet connectivity"""
  if not sm.updated['deviceState']:
    return False

  network_type = sm['deviceState'].networkType
  return network_type != log.DeviceState.NetworkType.none

def wazed_thread(alert_manager):
  """Background thread to fetch Waze alerts and check GPS data"""
  sm = messaging.SubMaster(['gpsLocation', 'deviceState'])
  params = Params()

  # For periodic GPS logging
  last_gps_log_time = 0
  GPS_LOG_INTERVAL = 60  # Log GPS position every minute

  # For checking if service is enabled
  last_enabled_check_time = 0
  last_network_status = False
  network_status_check_time = 0
  NETWORK_STATUS_CHECK_INTERVAL = 10  # Check network status every 10 seconds

  # Initialize default parameters
  initialize_default_params()

  # Track service enabled state for logging changes
  service_enabled = params.get_bool("WazeAlertsEnabled")
  logger.info(f"Wazed service starting with enabled={service_enabled}")

  # Clear any active alerts at startup if the service is disabled
  if not service_enabled:
    alert_manager.active_alert = None
    alert_manager.publish_waze_alert_message()

  while True:
    current_time = time.time()

    # Check if service is enabled periodically
    if current_time - last_enabled_check_time >= ENABLED_CHECK_INTERVAL:
      previous_state = service_enabled
      service_enabled = params.get_bool("WazeAlertsEnabled")
      last_enabled_check_time = current_time

      # Log state changes
      if service_enabled != previous_state:
        if service_enabled:
          logger.info("Waze Alerts service has been enabled")
          # Reset API call time to force an update when re-enabled
          global last_api_call_time
          last_api_call_time = 0
        else:
          logger.info("Waze Alerts service has been disabled")
          # Clear any active alerts
          if alert_manager.active_alert:
            alert_manager.active_alert = None
            alert_manager.publish_waze_alert_message()

    # Skip processing if service is disabled
    if not service_enabled:
      time.sleep(ENABLED_CHECK_INTERVAL)
      continue

    # Normal processing when enabled
    sm.update()

    if sm.updated['gpsLocation']:
      gps = sm['gpsLocation']

      # Get the car's position and bearing
      alert_manager.current_lat = gps.latitude
      alert_manager.current_lon = gps.longitude
      alert_manager.bearing = gps.bearingDeg if gps.bearingDeg > 0.0 else 0.0  # Use GPS bearing when available

      # Log GPS position periodically
      if current_time - last_gps_log_time > GPS_LOG_INTERVAL:
        cloudlog.info(f"Wazed: Current position: lat={alert_manager.current_lat:.6f}, lon={alert_manager.current_lon:.6f}, bearing={alert_manager.bearing:.1f}°")
        logger.info(f"Current position: lat={alert_manager.current_lat:.6f}, lon={alert_manager.current_lon:.6f}, bearing={alert_manager.bearing:.1f}°")
        last_gps_log_time = current_time

      # Check network status periodically
      if current_time - network_status_check_time >= NETWORK_STATUS_CHECK_INTERVAL:
        network_status_check_time = current_time
        is_online = has_internet(sm)

        # Log when network status changes
        if is_online != last_network_status:
          last_network_status = is_online
          if is_online:
            logger.info("Internet connection detected - Waze alerts will be updated")
          else:
            logger.info("No internet connection - Using cached Waze alerts only")

      # Only attempt to fetch Waze alerts every WAZE_API_UPDATE_INTERVAL
      elapsed_since_last_call = current_time - last_api_call_time
      if elapsed_since_last_call >= WAZE_API_UPDATE_INTERVAL and not api_is_busy:
        # Fetch Waze alerts and update the global alerts_cache
        fetch_waze_alerts(alert_manager.current_lat, alert_manager.current_lon, last_network_status)

    # Check for approaching alerts
    alert_manager.check_alerts()

    time.sleep(CHECK_ALERTS_INTERVAL)

def main():
  logger.info("Wazed: Starting Waze Alerts extension")

  # Create WazeAlertManager
  alert_manager = WazeAlertManager()

  # Create a PubMaster for publishing messages
  alert_manager.pm = messaging.PubMaster(['wazeAlerts', 'onroadEvents'])

  # Initialize with empty alert
  alert_manager.publish_waze_alert_message()
  logger.info("Sent initial wazeAlerts message")

  # Start the background thread for fetching alerts and GPS data
  fetch_thread = threading.Thread(target=wazed_thread, args=(alert_manager,), daemon=True)
  fetch_thread.start()

  # Keep main thread alive
  while True:
    time.sleep(10)

if __name__ == "__main__":
  main()
