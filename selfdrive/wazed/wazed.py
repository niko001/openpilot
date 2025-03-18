#!/usr/bin/env python3
import json
import math
import time
import threading
import requests
import logging
import queue
from datetime import datetime

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

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wazed")

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

def fetch_waze_alerts(lat, lon):
  """Fetch alerts from the Waze API using the specified coordinates as the center of the bounding box."""
  global last_api_call_time, alerts_cache, api_is_busy

  current_time = time.time()

  # Only update once every WAZE_API_UPDATE_INTERVAL and ensure we're not already making an API call
  if current_time - last_api_call_time < WAZE_API_UPDATE_INTERVAL or api_is_busy:
    return alerts_cache  # Return cache even if empty (will be an empty list, not None)

  api_is_busy = True  # Set flag to prevent concurrent API calls

  # Calculate bounding box
  half_width = BOUNDING_BOX_WIDTH / 2
  top = lat + half_width
  bottom = lat - half_width
  left = lon - half_width
  right = lon + half_width

  # Construct the API URL
  url = f"https://www.waze.com/live-map/api/georss?top={top}&bottom={bottom}&left={left}&right={right}&env=row&types=alerts"

  try:
    logger.info(f"Wazed: Making API request to {url}")
    last_api_call_time = current_time
    response = requests.get(url, timeout=10)
    logger.info(f"Wazed: Response status: {response.status_code}")

    if response.status_code == 200:
      response_text = response.text
      logger.info(f"Wazed: Response content: {response_text[:500]}..." if len(response_text) > 500 else response_text)

      try:
        data = response.json()
        if "alerts" in data:
          # Get real alerts from API
          alerts_cache = data["alerts"]
          cloudlog.info(f"Wazed: Fetched {len(alerts_cache)} alerts from Waze API (including mock alert)")
          logger.info(f"Fetched {len(alerts_cache)} alerts from Waze API (including mock alert)")
        else:
          cloudlog.warning(f"Wazed: No alerts field in Waze API response. Response keys: {data.keys()}")
          logger.warning(f"No alerts field in Waze API response. Response keys: {data.keys()}")
      except json.JSONDecodeError as je:
        cloudlog.error(f"Wazed: JSON parsing error: {je}")
        logger.error(f"JSON parsing error: {je}")
    else:
      cloudlog.warning(f"Wazed: Non-200 response from API: {response.status_code}")
      logger.warning(f"Non-200 response from API: {response.status_code}")
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
  street = alert.get("street", "")
  description = alert.get("reportDescription", "")

  # Format title based on alert type
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
    text += f"\n{description}"

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
  elif subtype == "SPEED_CAMERA":
    return WAZE_ALERT_SPEED_CAMERA
  elif subtype == "REDLIGHT_CAMERA":
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
    # Default to enabled if setting doesn't exist
    if alert_type == "HAZARD":
      return params.get_bool("WazeAlertsHazards", True)
    elif alert_type == "JAM":
      return params.get_bool("WazeAlertsJams", True)
    elif alert_type == "ACCIDENT":
      return params.get_bool("WazeAlertsAccidents", True)
    elif alert_type == "POLICE":
      return params.get_bool("WazeAlertsPolice", True)
    elif alert_type == "ROAD_CLOSED":
      return params.get_bool("WazeAlertsRoadClosed", True)
    elif alert_subtype == "SPEED_CAMERA":
      return params.get_bool("WazeAlertsSpeedCameras", True)
    elif alert_subtype == "REDLIGHT_CAMERA":
      return params.get_bool("WazeAlertsRedLightCameras", True)
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

def wazed_thread(alert_manager):
  """Background thread to fetch Waze alerts and check GPS data"""
  sm = messaging.SubMaster(['gpsLocationExternal'])
  params = Params()

  # For periodic GPS logging
  last_gps_log_time = 0
  GPS_LOG_INTERVAL = 60  # Log GPS position every minute

  # For checking if service is enabled
  last_enabled_check_time = 0

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

    if sm.updated['gpsLocationExternal']:
      gps = sm['gpsLocationExternal']

      # Get the car's position and bearing
      alert_manager.current_lat = gps.latitude
      alert_manager.current_lon = gps.longitude
      alert_manager.bearing = gps.bearingDeg if gps.bearingDeg > 0.0 else 0.0  # Use GPS bearing when available

      # Log GPS position periodically
      if current_time - last_gps_log_time > GPS_LOG_INTERVAL:
        cloudlog.info(f"Wazed: Current position: lat={alert_manager.current_lat:.6f}, lon={alert_manager.current_lon:.6f}, bearing={alert_manager.bearing:.1f}°")
        logger.info(f"Current position: lat={alert_manager.current_lat:.6f}, lon={alert_manager.current_lon:.6f}, bearing={alert_manager.bearing:.1f}°")
        last_gps_log_time = current_time

      # Only attempt to fetch Waze alerts every WAZE_API_UPDATE_INTERVAL
      elapsed_since_last_call = current_time - last_api_call_time
      if elapsed_since_last_call >= WAZE_API_UPDATE_INTERVAL and not api_is_busy:
        # Fetch Waze alerts and update the global alerts_cache
        fetch_waze_alerts(alert_manager.current_lat, alert_manager.current_lon)

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
