#!/usr/bin/env python3
import json
import math
import time
import threading
import requests
import logging
from datetime import datetime, timedelta

import cereal.messaging as messaging
from cereal import log
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.selfdrived.events import Alert, AlertStatus, AlertSize, Priority, VisualAlert, AudibleAlert

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wazed")

# Constants for the Waze alerts module
WAZE_API_UPDATE_INTERVAL = 120  # Update every 2 minutes (in seconds)
BOUNDING_BOX_WIDTH = 0.07  # About 5-7 km depending on latitude
ALERT_DISTANCE_THRESHOLD = 200  # Meters, distance to trigger alert
CHECK_ALERTS_INTERVAL = 1.0  # Check for alerts every 1 second

# Global variables
alerts_cache = []
last_api_call_time = 0
last_alerted_uuids = set()  # To prevent showing the same alert multiple times in succession

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

def is_approaching(car_lat, car_lon, car_bearing, alert_lat, alert_lon, threshold=ALERT_DISTANCE_THRESHOLD):
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
  global last_api_call_time, alerts_cache

  current_time = time.time()

  # Only update once every WAZE_API_UPDATE_INTERVAL
  if current_time - last_api_call_time < WAZE_API_UPDATE_INTERVAL and alerts_cache:
    return alerts_cache

  # Calculate bounding box
  half_width = BOUNDING_BOX_WIDTH / 2
  top = lat + half_width
  bottom = lat - half_width
  left = lon - half_width
  right = lon + half_width

  # Construct the API URL
  url = f"https://www.waze.com/live-map/api/georss?top={top}&bottom={bottom}&left={left}&right={right}&env=row&types=alerts"

  try:
    response = requests.get(url, timeout=10)
    if response.status_code == 200:
      data = response.json()
      if "alerts" in data:
        alerts_cache = data["alerts"]
        last_api_call_time = current_time
        cloudlog.info(f"Wazed: Fetched {len(alerts_cache)} alerts from Waze API")
        logger.info(f"Fetched {len(alerts_cache)} alerts from Waze API")
        return alerts_cache
      else:
        cloudlog.warning("Wazed: No alerts field in Waze API response")
        logger.warning("No alerts field in Waze API response")
  except Exception as e:
    cloudlog.error(f"Wazed: Error fetching Waze alerts: {e}")
    logger.error(f"Error fetching Waze alerts: {e}")

  # Return cached data if request fails
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

def create_waze_alert(alert):
  """Create an OpenPilot alert for a Waze alert."""
  title, text = get_alert_text(alert)

  alert_type = alert.get("type", "UNKNOWN")

  # Set alert parameters based on type
  if alert_type in ["ACCIDENT", "POLICE", "HAZARD"]:
    # Higher priority for accidents and hazards
    priority = Priority.MID
    audible = AudibleAlert.prompt
  else:
    priority = Priority.LOW
    audible = AudibleAlert.none

  return Alert(
    title, text,
    AlertStatus.normal, AlertSize.mid,
    priority, VisualAlert.none, audible, 5.0
  )

def wazed_thread():
  """Background thread to fetch Waze alerts."""
  global alerts_cache

  pm = messaging.PubMaster(['wazeAlerts'])
  sm = messaging.SubMaster(['liveLocationKalman'])

  # For periodic GPS logging
  last_gps_log_time = 0
  GPS_LOG_INTERVAL = 60  # Log GPS position every minute

  while True:
    sm.update()

    if sm.updated['liveLocationKalman']:
      loc = sm['liveLocationKalman'].getLiveLocationKalman()

      if not loc.getGpsOK():
        time.sleep(1)
        continue

      # Get the car's position and bearing
      car_lat = loc.getPositionGeodetic().getValue()[0]
      car_lon = loc.getPositionGeodetic().getValue()[1]
      car_bearing = math.degrees(loc.getOrientationNED().getValue()[2]) % 360

      # Log GPS position periodically
      current_time = time.time()
      if current_time - last_gps_log_time > GPS_LOG_INTERVAL:
        cloudlog.info(f"Wazed: Current position: lat={car_lat:.6f}, lon={car_lon:.6f}, bearing={car_bearing:.1f}°")
        logger.info(f"Current position: lat={car_lat:.6f}, lon={car_lon:.6f}, bearing={car_bearing:.1f}°")
        last_gps_log_time = current_time

      # Fetch Waze alerts
      waze_alerts = fetch_waze_alerts(car_lat, car_lon)

      # Send the alerts to our subscribers
      waze_alert_msg = messaging.new_message('wazeAlerts')
      waze_alert_msg.wazeAlerts.position.latitude = car_lat
      waze_alert_msg.wazeAlerts.position.longitude = car_lon
      waze_alert_msg.wazeAlerts.bearing = car_bearing

      alerts_to_check = []
      for alert in waze_alerts:
        if 'location' in alert and 'x' in alert['location'] and 'y' in alert['location']:
          alert_lat = alert['location']['y']
          alert_lon = alert['location']['x']
          is_ahead, distance = is_approaching(car_lat, car_lon, car_bearing, alert_lat, alert_lon)

          if is_ahead:
            alert_data = {
              'uuid': alert.get('uuid', ''),
              'type': alert.get('type', 'UNKNOWN'),
              'latitude': alert_lat,
              'longitude': alert_lon,
              'distance': distance,
              'street': alert.get('street', ''),
              'description': alert.get('reportDescription', ''),
              'subtype': alert.get('subtype', '')
            }
            alerts_to_check.append(alert_data)

      waze_alert_msg.wazeAlerts.alertsCount = len(alerts_to_check)

      pm.send('wazeAlerts', waze_alert_msg)

    time.sleep(CHECK_ALERTS_INTERVAL)

def check_alerts_thread():
  """Thread to check if we need to display alerts to the user."""
  global last_alerted_uuids

  sm = messaging.SubMaster(['wazeAlerts', 'selfdriveState'])

  while True:
    sm.update()

    if sm.updated['wazeAlerts'] and sm.updated['selfdriveState']:
      # Only show alerts when the car is started
      if not sm['selfdriveState'].getSelfdriveState().getStarted():
        time.sleep(1)
        continue

      alerts_to_show = []

      # Get the car's position
      car_lat = sm['wazeAlerts'].getWazeAlerts().getPosition().getLatitude()
      car_lon = sm['wazeAlerts'].getWazeAlerts().getPosition().getLongitude()
      car_bearing = sm['wazeAlerts'].getWazeAlerts().getBearing()

      # Check each alert in the cache
      for alert in alerts_cache:
        if 'location' not in alert or 'x' not in alert['location'] or 'y' not in alert['location']:
          continue

        alert_lat = alert['location']['y']
        alert_lon = alert['location']['x']
        alert_uuid = alert.get('uuid', '')

        is_ahead, distance = is_approaching(car_lat, car_lon, car_bearing, alert_lat, alert_lon)

        # If we're approaching this alert and haven't alerted about it recently
        if is_ahead and alert_uuid not in last_alerted_uuids:
          op_alert = create_waze_alert(alert)

          # Construct and send controlsState message with our alert
          # This part depends on how openpilot handles custom alerts...
          alert_message = f"{op_alert.alert_text_1} - {op_alert.alert_text_2}"
          cloudlog.warning(f"Wazed: ALERT TRIGGERED: {alert_message} - Distance: {distance:.1f}m - Type: {alert.get('type', 'UNKNOWN')}")
          logger.warning(f"ALERT TRIGGERED: {alert_message} - Distance: {distance:.1f}m - Type: {alert.get('type', 'UNKNOWN')}")

          # For demo, also print to console
          print(f"WAZE ALERT: {alert_message}")

          # Add to alerted set to prevent repeat alerts
          last_alerted_uuids.add(alert_uuid)

          # Clean up old UUIDs occasionally (keep max 20)
          if len(last_alerted_uuids) > 20:
            last_alerted_uuids.pop()

      # Log alert statistics
      if len(alerts_cache) > 0:
        cloudlog.debug(f"Wazed: Monitoring {len(alerts_cache)} alerts, {len(last_alerted_uuids)} already alerted")

    time.sleep(CHECK_ALERTS_INTERVAL)

def main():
  # Start the background threads
  threads = [
    threading.Thread(target=wazed_thread, daemon=True),
    threading.Thread(target=check_alerts_thread, daemon=True)
  ]

  for t in threads:
    t.start()

  # Keep the main thread alive
  while True:
    time.sleep(10)

if __name__ == "__main__":
  main()
