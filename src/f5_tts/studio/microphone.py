"""Browser microphone control; captured audio is sent only after confirmation."""

from nicegui.element import Element


class Microphone(Element, component="microphone.js"):
    def __init__(self, on_recorded):
        super().__init__()
        self.on("recorded", on_recorded)
