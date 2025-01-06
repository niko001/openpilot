#!/usr/bin/env python3
import os
import time
import json
import sqlite3
import threading
from math import radians, sin, cos, sqrt, atan2
import requests

from cereal import messaging, log
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.numpy_fast import interp

EARTH_RADIUS_KM = 6371  # Radius of the earth in km

def haversine_distance(lat1, lon1, lat2, lon2):
  """Calculate the great circle distance between two points on the earth."""
  lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
  dlat = lat2 - lat1
  dlon = lon2 - lon1
  a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
  c = 2 * atan2(sqrt(a), sqrt(1-a))
  return EARTH_RADIUS_KM * c

class WazeAlertManager:
  def __init__(self):
    self.db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "waze_alerts.db")
    print(f"Initializing WazeAlertManager with database at: {self.db_path}")
    self.db = sqlite3.connect(self.db_path)
    self.setup_database()

    self.pm = messaging.PubMaster(['wazeAlerts'])
    self.sm = messaging.SubMaster(['gpsLocationExternal'])

    self.current_lat = 0
    self.current_lon = 0
    self.last_fetch_time = 0
    self.fetch_interval = 120  # seconds (2 minutes)
    self.alert_radius = 10  # km
    self.area_size = 10  # km (10x10 km area)

    self.active_alerts = set()  # Currently active alert UUIDs

  def setup_database(self):
    """Initialize the SQLite database schema."""
    print("Setting up database schema...")
    self.db.execute("""
      CREATE TABLE IF NOT EXISTS alerts (
        uuid TEXT PRIMARY KEY,
        type TEXT,
        subtype TEXT,
        latitude REAL,
        longitude REAL,
        pub_millis INTEGER,
        road_name TEXT,
        city TEXT
      )
    """)
    self.db.commit()
    print("Database schema setup complete")

  def fetch_alerts(self):
    """Fetch alerts from Waze API for current location."""
    if not self.current_lat or not self.current_lon:
      print("No GPS coordinates available yet, skipping alert fetch")
      return

    print(f"\nFetching alerts for coordinates: lat={self.current_lat}, lon={self.current_lon}")

    # Calculate bounding box
    km_per_degree = 111.32  # approximate degrees per km at equator
    lat_delta = self.area_size / km_per_degree
    lon_delta = self.area_size / (km_per_degree * cos(radians(self.current_lat)))

    bounds = {
      'top': self.current_lat + lat_delta,
      'bottom': self.current_lat - lat_delta,
      'left': self.current_lon - lon_delta,
      'right': self.current_lon + lon_delta
    }

    print(f"Bounding box: {json.dumps(bounds, indent=2)}")

    try:
      url = "https://www.waze.com/live-map/api/georss"
      params = {
        'top': bounds['top'],
        'bottom': bounds['bottom'],
        'left': bounds['left'],
        'right': bounds['right'],
        'env': 'row',
        'types': 'alerts'
      }

      # Log the full URL being requested
      full_url = requests.Request('GET', url, params=params).prepare().url
      print(f"Requesting Waze alerts from: {full_url}")

      response = requests.get(url, params=params, timeout=10)
      print(f"Response status code: {response.status_code}")

      data = response.json()
      print(f"Response data: {json.dumps(data, indent=2)}")

      # Clear old alerts from database
      #self.db.execute("DELETE FROM alerts")
      print("Cleared old alerts from database")

      # Store new alerts
      alerts = data.get('alerts', [])
      print(f"Found {len(alerts)} alerts")

      for alert in alerts:
        self.db.execute("""
          INSERT INTO alerts (uuid, type, subtype, latitude, longitude, pub_millis, road_name, city)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
          alert['uuid'],
          alert['type'],
          alert.get('subtype', ''),
          alert['location']['y'],
          alert['location']['x'],
          alert['pubMillis'],
          alert.get('street', ''),
          alert.get('city', '')
        ))
        print(f"Stored alert: type={alert['type']}, subtype={alert.get('subtype', '')}, location=({alert['location']['y']}, {alert['location']['x']})")

      self.db.commit()
      print("Successfully committed alerts to database")

    except Exception as e:
      print(f"Error fetching Waze alerts: {e.__class__.__name__}: {str(e)}")
      if hasattr(e, 'response'):
        print(f"Response status code: {e.response.status_code}")
        print(f"Response content: {e.response.text}")

  def check_alerts(self):
    """Check for alerts near current location."""
    if not self.current_lat or not self.current_lon:
      return set()

    # Query database for nearby alerts
    cursor = self.db.execute("""
      SELECT uuid, type, subtype, latitude, longitude, road_name, city
      FROM alerts
      WHERE pub_millis > ?
    """, (int(time.time()*1000) - 10800000,))  # Only get alerts from last 3 hours

    nearby_alerts = set()
    for alert in cursor.fetchall():
      distance = haversine_distance(
        self.current_lat, self.current_lon,
        alert[3], alert[4]
      )

      if distance <= self.alert_radius:
        nearby_alerts.add(alert[0])  # Add UUID to nearby alerts

        if alert[0] not in self.active_alerts:
          print(f"New nearby alert detected: type={alert[1]}, subtype={alert[2]}, distance={distance:.2f}km")

          # Map Waze alert types to openpilot alert types
          alert_type = None
          if alert[1] == "POLICE":
            alert_type = "wazePolice"
          elif alert[1] == "HAZARD":
            alert_type = "wazeHazard"
          elif alert[1] == "ACCIDENT":
            alert_type = "wazeAccident"
          elif alert[1] == "ROAD_CLOSED":
            alert_type = "wazeRoadClosed"

          if alert_type:
            # Create alert event
            alert_event = messaging.new_message('controlsState')
            alert_event.controlsState.alertType = alert_type
            alert_event.controlsState.alertText1 = f"{alert[5] or 'Unknown Road'}"
            alert_event.controlsState.alertText2 = f"{distance:.1f}km ahead"
            alert_event.controlsState.alertSize = log.ControlsState.AlertSize.small
            alert_event.controlsState.alertStatus = log.ControlsState.AlertStatus.normal
            alert_event.controlsState.alertBlinkingRate = 0.
            alert_event.controlsState.alertSound = log.CarControl.HUDControl.AudibleAlert.warningImmediate

            self.pm.send('controlsState', alert_event)

    # Update active alerts
    self.active_alerts = nearby_alerts
    return nearby_alerts

  def update(self):
    """Main update loop."""
    self.sm.update()

    if self.sm.updated['gpsLocationExternal']:
      prev_lat = self.current_lat
      prev_lon = self.current_lon
      self.current_lat = self.sm['gpsLocationExternal'].latitude
      self.current_lon = self.sm['gpsLocationExternal'].longitude

      if prev_lat != self.current_lat or prev_lon != self.current_lon:
        print(f"Updated GPS location: lat={self.current_lat}, lon={self.current_lon}")

    current_time = time.time()
    if current_time - self.last_fetch_time >= self.fetch_interval:
      print(f"\nTime to fetch alerts (last fetch was {current_time - self.last_fetch_time:.1f}s ago)")
      self.fetch_alerts()
      self.last_fetch_time = current_time

    self.check_alerts()

def main():
  print("Starting WazeAlertManager...")
  waze = WazeAlertManager()
  rk = Ratekeeper(2.0)  # 2Hz update rate

  while True:
    waze.update()
    rk.keep_time()

if __name__ == "__main__":
  main()
