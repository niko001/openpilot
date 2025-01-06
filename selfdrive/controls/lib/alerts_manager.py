#!/usr/bin/env python3
import json
import os
import time
from typing import Dict, Optional, Set
from math import isfinite

from openpilot.common.basedir import BASEDIR
import cereal.messaging as messaging

class AlertsManager:
  def __init__(self, sm=None, pm=None):
    self.sm = messaging.SubMaster(['deviceState', 'pandaStates', 'roadCameraState',
                               'modelV2', 'liveCalibration', 'driverMonitoringState',
                               'longitudinalPlan', 'pandaStates', 'deviceState',
                               'wazeAlerts']) if sm is None else sm
    self.pm = messaging.PubMaster(['controlsState']) if pm is None else pm

    # Load alert configurations
    alert_types = json.load(open(os.path.join(BASEDIR, "selfdrive/controls/lib/alerts_offroad.json")))
    waze_alerts = json.load(open(os.path.join(BASEDIR, "selfdrive/controls/lib/alerts_waze.json")))
    alert_types.update(waze_alerts)

    self.alerts = alert_types
    self.active_alerts: Set[str] = set()
    self.alert_start_times: Dict[str, float] = {}
    self.alert_rate_limits: Dict[str, float] = {}
    self.alert_last_displayed: Dict[str, float] = {}

  def add(self, alert_name: str, enabled: bool = True, extra_text: str = "") -> None:
    if not isinstance(alert_name, str):
      return

    alert = self.alerts.get(alert_name)
    if alert is None:
      return

    # Don't add if alert is already active
    if alert_name in self.active_alerts:
      return

    # Rate limit alerts
    current_time = time.monotonic()
    if alert_name in self.alert_last_displayed:
      if current_time - self.alert_last_displayed[alert_name] < self.alert_rate_limits.get(alert_name, 5.0):
        return

    if enabled:
      self.active_alerts.add(alert_name)
      self.alert_start_times[alert_name] = current_time
      self.alert_last_displayed[alert_name] = current_time

  def remove(self, alert_name: str) -> None:
    if alert_name in self.active_alerts:
      self.active_alerts.remove(alert_name)
      if alert_name in self.alert_start_times:
        del self.alert_start_times[alert_name]

  def clear_current_alert(self) -> None:
    self.active_alerts.clear()
    self.alert_start_times.clear()

  def process_alerts(self, clear: bool = False) -> None:
    if clear:
      self.clear_current_alert()

    cur_time = time.monotonic()

    # Check for expired alerts
    alerts_to_remove = set()
    for alert_name in self.active_alerts.copy():
      start_time = self.alert_start_times.get(alert_name, 0)
      if cur_time - start_time > self.alerts[alert_name].get("duration", float("inf")):
        alerts_to_remove.add(alert_name)

    for alert_name in alerts_to_remove:
      self.remove(alert_name)

  def update(self) -> None:
    self.sm.update()

    # Handle Waze alerts
    if self.sm.updated['wazeAlerts']:
      alert = self.sm['wazeAlerts']
      if isfinite(alert.distance) and alert.alertType:
        if alert.alertType == "POLICE":
          self.add("wazePolice", True)
        elif alert.alertType == "HAZARD":
          self.add("wazeHazard", True)
        elif alert.alertType == "ACCIDENT":
          self.add("wazeAccident", True)
        elif alert.alertType == "ROAD_CLOSED":
          self.add("wazeRoadClosed", True)

    # Process alerts
    self.process_alerts()

    # Send alert status
    alert_status = messaging.new_message('controlsState')
    alert_status.controlsState.alertText1 = ""
    alert_status.controlsState.alertText2 = ""
    alert_status.controlsState.alertSize = 0
    alert_status.controlsState.alertStatus = 0
    alert_status.controlsState.alertBlinkingRate = 0.

    if len(self.active_alerts) > 0:
      current_alert = list(self.active_alerts)[0]  # Get highest priority alert
      alert_config = self.alerts[current_alert]

      alert_text = alert_config["text"]
      if isinstance(alert_text, list):
        alert_status.controlsState.alertText1 = alert_text[0]
        if len(alert_text) > 1:
          alert_status.controlsState.alertText2 = alert_text[1]
      else:
        alert_status.controlsState.alertText1 = alert_text

      alert_status.controlsState.alertSize = alert_config.get("size", 1)
      alert_status.controlsState.alertStatus = alert_config.get("severity", 0)
      alert_status.controlsState.alertBlinkingRate = alert_config.get("blinkingRate", 0.)

    self.pm.send('controlsState', alert_status)

def main():
  am = AlertsManager()
  while True:
    am.update()
    time.sleep(0.1)

if __name__ == "__main__":
  main()
