#!/usr/bin/env python3
import os
import time
import json
import sqlite3
import threading
from math import radians, sin, cos, sqrt, atan2
import requests

import cereal.messaging as messaging
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
    self.db = sqlite3.connect(self.db_path)
    self.setup_database()

    self.pm = messaging.PubMaster(['wazeAlerts'])
    self.sm = messaging.SubMaster(['gpsLocationExternal'])

    self.current_lat = 0
    self.current_lon = 0
    self.last_fetch_time = 0
    self.fetch_interval = 30  # seconds
    self.alert_radius = 5  # km
    self.area_size = 5  # km (5x5 km area)

    self.active_alerts = set()  # Currently active alert UUIDs

  def setup_database(self):
    """Initialize the SQLite database schema."""
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

  def fetch_alerts(self):
    """Fetch alerts from Waze API for current location."""
    if not self.current_lat or not self.current_lon:
      return

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

    try:
      url = f"https://www.waze.com/live-map/api/georss"
      params = {
        'top': bounds['top'],
        'bottom': bounds['bottom'],
        'left': bounds['left'],
        'right': bounds['right'],
        'env': 'row',
        'types': 'alerts'
      }

      response = requests.get(url, params=params, timeout=10)
      data = response.json()

      # Clear old alerts from database
      self.db.execute("DELETE FROM alerts")

      # Store new alerts
      for alert in data.get('alerts', []):
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

      self.db.commit()

    except Exception as e:
      print(f"Error fetching Waze alerts: {e}")

  def check_alerts(self):
    """Check for alerts near current location."""
    if not self.current_lat or not self.current_lon:
      return set()

    # Query database for nearby alerts
    cursor = self.db.execute("""
      SELECT uuid, type, subtype, latitude, longitude, road_name, city
      FROM alerts
      WHERE pub_millis > ?
    """, (int(time.time()*1000) - 3600000,))  # Only get alerts from last hour

    nearby_alerts = set()
    for alert in cursor.fetchall():
      distance = haversine_distance(
        self.current_lat, self.current_lon,
        alert[3], alert[4]
      )

      if distance <= self.alert_radius:
        nearby_alerts.add(alert[0])  # Add UUID to nearby alerts

        if alert[0] not in self.active_alerts:
          # New alert detected - send alert message
          alert_msg = messaging.new_message('wazeAlerts')
          alert_msg.wazeAlerts = {
            'alertType': alert[1],
            'alertSubType': alert[2],
            'distance': distance,
            'roadName': alert[5],
            'city': alert[6]
          }
          self.pm.send('wazeAlerts', alert_msg)

    # Update active alerts
    self.active_alerts = nearby_alerts
    return nearby_alerts

  def update(self):
    """Main update loop."""
    self.sm.update()

    if self.sm.updated['gpsLocationExternal']:
      self.current_lat = self.sm['gpsLocationExternal'].latitude
      self.current_lon = self.sm['gpsLocationExternal'].longitude

    current_time = time.time()
    if current_time - self.last_fetch_time >= self.fetch_interval:
      self.fetch_alerts()
      self.last_fetch_time = current_time

    self.check_alerts()

def main():
  waze = WazeAlertManager()
  rk = Ratekeeper(2.0)  # 2Hz update rate

  while True:
    waze.update()
    rk.keep_time()

if __name__ == "__main__":
  main()
