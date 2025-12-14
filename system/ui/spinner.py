#!/usr/bin/env python3
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
DARKGRAY = (55, 55, 55, 255)


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

  def set_text(self, text: str) -> None:
    if text.isdigit():
      self._progress = clamp(int(text), 0, 100)
      self._wrapped_lines = []
    else:
      self._progress = None
      self._wrapped_lines = wrap_text(text, FONT_SIZE, gui_app.width - MARGIN_H)

  def _render(self, rect: rl.Rectangle):
    self._draw_background(rect)

    center_x = rect.width / 2.0
    if self._progress is not None:
      y_pos = rect.height / 2.0 - PROGRESS_BAR_HEIGHT / 2.0
      bar = rl.Rectangle(center_x - PROGRESS_BAR_WIDTH / 2.0, y_pos, PROGRESS_BAR_WIDTH, PROGRESS_BAR_HEIGHT)
      rl.draw_rectangle_rounded(bar, 1, 10, DARKGRAY)

      bar.width *= self._progress / 100.0
      rl.draw_rectangle_rounded(bar, 1, 10, rl.WHITE)
    elif self._wrapped_lines:
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
