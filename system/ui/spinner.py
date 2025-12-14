#!/usr/bin/env python3
import math
import pyray as rl
import select
import sys

from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.text import wrap_text
from openpilot.system.ui.widgets import Widget

# Constants
if gui_app.big_ui():
  PROGRESS_BAR_WIDTH = 1000
  PROGRESS_BAR_HEIGHT = 20
else:
  PROGRESS_BAR_WIDTH = 268
  PROGRESS_BAR_HEIGHT = 10
MARGIN_H = 100
FONT_SIZE = 96
LINE_HEIGHT = 104

LASER_TRACK_COLOR = rl.Color(0, 0, 0, 120)
LASER_TRACK_INNER_COLOR = rl.Color(0, 0, 0, 70)
LASER_BORDER_COLOR = rl.Color(80, 190, 255, 110)
LASER_CORE_L = rl.Color(70, 240, 255, 235)
LASER_CORE_R = rl.Color(0, 110, 255, 235)


def clamp(value, min_value, max_value):
  return max(min(value, max_value), min_value)


class Spinner(Widget):
  def __init__(self):
    super().__init__()
    self._background_texture = gui_app.texture("../../sunnypilot/selfdrive/assets/images/spinner_sunnypilot.png")
    self._progress: int | None = None
    self._wrapped_lines: list[str] = []

  def _draw_background(self, rect: rl.Rectangle) -> None:
    tex_w = float(self._background_texture.width)
    tex_h = float(self._background_texture.height)
    if tex_w <= 0.0 or tex_h <= 0.0:
      return

    scale = max(rect.width / tex_w, rect.height / tex_h)
    dst_w = tex_w * scale
    dst_h = tex_h * scale

    dst_rect = rl.Rectangle(
      rect.x + (rect.width - dst_w) / 2.0,
      rect.y + (rect.height - dst_h) / 2.0,
      dst_w,
      dst_h,
    )
    rl.draw_texture_pro(
      self._background_texture,
      rl.Rectangle(0.0, 0.0, tex_w, tex_h),
      dst_rect,
      rl.Vector2(0.0, 0.0),
      0.0,
      rl.WHITE,
    )

  def _draw_laser_progress_bar(self, rect: rl.Rectangle, progress: int) -> None:
    center_x = rect.width / 2.0
    # Slightly below center to avoid overlapping the background artwork's focal point
    y_pos = rect.height * 0.77 - PROGRESS_BAR_HEIGHT / 2.0
    track = rl.Rectangle(center_x - PROGRESS_BAR_WIDTH / 2.0, y_pos, PROGRESS_BAR_WIDTH, PROGRESS_BAR_HEIGHT)

    # Track with slight shadow so it reads on bright backgrounds
    shadow = rl.Rectangle(track.x, track.y + 2.0, track.width, track.height)
    rl.draw_rectangle_rounded(shadow, 1.0, 10, rl.Color(0, 0, 0, 160))
    rl.draw_rectangle_rounded(track, 1.0, 10, LASER_TRACK_COLOR)

    inner_pad = max(2.0, track.height * 0.18)
    inner = rl.Rectangle(track.x + inner_pad, track.y + inner_pad, track.width - 2.0 * inner_pad, track.height - 2.0 * inner_pad)
    if inner.width > 0.0 and inner.height > 0.0:
      rl.draw_rectangle_rounded(inner, 1.0, 10, LASER_TRACK_INNER_COLOR)

    fill_w = track.width * (progress / 100.0)
    if fill_w <= 0.5:
      rl.draw_rectangle_rounded_lines_ex(track, 1.0, 10, 2, LASER_BORDER_COLOR)
      return

    fill_w = min(fill_w, track.width)

    t = rl.get_time()
    pulse = 0.65 + 0.35 * (0.5 + 0.5 * math.sin(t * 5.5))
    speed = 520.0 if gui_app.big_ui() else 300.0

    glow_h = track.height * 3.0
    glow_y = track.y + (track.height - glow_h) / 2.0

    sc_x = int(track.x)
    sc_y = int(glow_y)
    sc_w = max(1, int(fill_w))
    sc_h = max(1, int(glow_h))
    rl.begin_scissor_mode(sc_x, sc_y, sc_w, sc_h)

    # Outer glow layers
    for i in range(3, 0, -1):
      layer_h = track.height * (1.0 + i * 0.9)
      layer_y = track.y + (track.height - layer_h) / 2.0
      alpha = int((28 + i * 10) * pulse)
      rl.draw_rectangle_gradient_h(int(track.x), int(layer_y), sc_w, max(1, int(layer_h)),
                                   rl.Color(0, 210, 255, alpha), rl.Color(0, 80, 255, alpha))

    # Bright core
    core_h = max(2.0, track.height * 0.55)
    core_y = track.y + (track.height - core_h) / 2.0
    rl.draw_rectangle_gradient_h(int(track.x), int(core_y), sc_w, max(1, int(core_h)), LASER_CORE_L, LASER_CORE_R)

    # Moving sparkle highlight
    sparkle_w = min(160.0, max(40.0, fill_w * 0.35))
    sparkle_x = track.x + (t * speed) % (fill_w + sparkle_w) - sparkle_w
    sparkle_h = track.height * 2.4
    sparkle_y = track.y + (track.height - sparkle_h) / 2.0
    half = sparkle_w / 2.0
    rl.draw_rectangle_gradient_h(int(sparkle_x), int(sparkle_y), max(1, int(half)), max(1, int(sparkle_h)),
                                 rl.Color(255, 255, 255, 0), rl.Color(255, 255, 255, 190))
    rl.draw_rectangle_gradient_h(int(sparkle_x + half), int(sparkle_y), max(1, int(sparkle_w - half)), max(1, int(sparkle_h)),
                                 rl.Color(255, 255, 255, 190), rl.Color(255, 255, 255, 0))

    rl.end_scissor_mode()

    rl.draw_rectangle_rounded_lines_ex(track, 1.0, 10, 2, LASER_BORDER_COLOR)

  def set_text(self, text: str) -> None:
    if text.isdigit():
      self._progress = clamp(int(text), 0, 100)
      self._wrapped_lines = []
    else:
      self._progress = None
      self._wrapped_lines = wrap_text(text, FONT_SIZE, gui_app.width - MARGIN_H)

  def _render(self, rect: rl.Rectangle):
    self._draw_background(rect)

    if self._progress is not None:
      self._draw_laser_progress_bar(rect, self._progress)
    elif self._wrapped_lines:
      center_x = rect.width / 2.0
      total_height = len(self._wrapped_lines) * LINE_HEIGHT
      y_pos = (rect.height - total_height) / 2.0
      for i, line in enumerate(self._wrapped_lines):
        text_size = measure_text_cached(gui_app.font(), line, FONT_SIZE)
        rl.draw_text_ex(gui_app.font(), line, rl.Vector2(center_x - text_size.x / 2, y_pos + i * LINE_HEIGHT),
                        FONT_SIZE, 0.0, rl.WHITE)


def _read_stdin():
  """Non-blocking read of available lines from stdin."""
  lines = []
  while True:
    rlist, _, _ = select.select([sys.stdin], [], [], 0.0)
    if not rlist:
      break
    line = sys.stdin.readline().strip()
    if line == "":
      break
    lines.append(line)
  return lines


def main():
  gui_app.init_window("Spinner")
  spinner = Spinner()
  for _ in gui_app.render():
    text_list = _read_stdin()
    if text_list:
      spinner.set_text(text_list[-1])

    spinner.render(rl.Rectangle(0, 0, gui_app.width, gui_app.height))


if __name__ == "__main__":
  main()
