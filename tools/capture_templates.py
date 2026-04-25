"""
Template capture tool — helps you screenshot the UI buttons the bot needs
to find via template matching.

Usage:
    python tools/capture_templates.py

Instructions:
  1. Open MTG Arena and navigate to where a button is visible.
  2. Run this script. A crosshair overlay will appear.
  3. Click and drag to draw a rectangle around the button.
  4. Enter a name (e.g. btn_pass, btn_ok, btn_keep, btn_mulligan).
  5. The cropped image is saved to src/vision/templates/<name>.png.
  6. Repeat for each button.

Required buttons:
  btn_keep.png     — "Keep" hand button (mulligan screen)
  btn_mulligan.png — "Mulligan" button (mulligan screen)

Optional (spacebar handles these via Arena's default action):
  btn_pass.png     — the "Pass" / "Pass Turn" button (bottom right)
  btn_ok.png       — the "OK" / confirm button
  mana_w/u/b/r/g/c.png — mana pip icons for mana detection
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import tkinter as tk
    from PIL import ImageGrab, Image
    import numpy as np
    import cv2
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: pip install pillow opencv-python")
    sys.exit(1)

TEMPLATE_DIR = Path(__file__).parent.parent / "src/vision/templates"
TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)


class TemplateCapture:
    def __init__(self):
        self.root = tk.Tk()
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-alpha", 0.3)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="black")

        self.canvas = tk.Canvas(self.root, cursor="crosshair", bg="black",
                                highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.start_x = self.start_y = 0
        self.rect = None

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Escape>", lambda e: self.root.destroy())

        label = tk.Label(self.root, text="Click and drag to select a button region. ESC to exit.",
                         fg="white", bg="black", font=("Arial", 14))
        label.place(x=20, y=20)

    def _on_press(self, event):
        self.start_x, self.start_y = event.x, event.y
        if self.rect:
            self.canvas.delete(self.rect)

    def _on_drag(self, event):
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, event.x, event.y,
            outline="lime", width=2
        )

    def _on_release(self, event):
        x1 = min(self.start_x, event.x)
        y1 = min(self.start_y, event.y)
        x2 = max(self.start_x, event.x)
        y2 = max(self.start_y, event.y)

        if (x2 - x1) < 5 or (y2 - y1) < 5:
            return

        self.root.withdraw()
        self.root.update()

        # Grab the selected region from the actual screen
        img = ImageGrab.grab(bbox=(x1, y1, x2, y2))

        name = input(f"\nRegion captured ({x2-x1}x{y2-y1}px). Enter template name (no .png): ").strip()
        if not name:
            print("Skipped.")
        else:
            out_path = TEMPLATE_DIR / f"{name}.png"
            img.save(out_path)
            print(f"Saved: {out_path}")

            # Show a preview
            arr = np.array(img)
            preview = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            cv2.imshow(f"Saved: {name}.png", preview)
            cv2.waitKey(1500)
            cv2.destroyAllWindows()

        another = input("Capture another? [y/N]: ").strip().lower()
        if another == "y":
            self.root.deiconify()
            self.root.attributes("-alpha", 0.3)
        else:
            self.root.destroy()

    def run(self):
        print("\nTemplate Capture Tool")
        print(f"Saving to: {TEMPLATE_DIR.resolve()}")
        print("Overlay active. Click and drag to capture a region.\n")
        self.root.mainloop()


if __name__ == "__main__":
    TemplateCapture().run()
