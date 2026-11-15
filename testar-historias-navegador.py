"""Opt-in browser checks with a temporary workspace and a fake synthesis backend."""

import asyncio
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4


WORKSPACE = Path(tempfile.gettempdir()) / "f5-studio-story-browser"
URL = "http://127.0.0.1:17863"


def serve():
    import numpy as np
    import soundfile as sf
    from nicegui import ui

    from f5_tts.studio.app import register_pages
    from f5_tts.studio.service import StudioService

    class BrowserModel:
        def __init__(self, **options):
            self.options = options

        def infer(self, **options):
            sf.write(options["file_wave"], np.ones(24000, dtype=np.float32) * 0.1, 24000)
            with (WORKSPACE / "calls.jsonl").open("a") as log:
                log.write(
                    json.dumps({"model": self.options, "voice": options["ref_file"], "text": options["gen_text"]})
                    + "\n"
                )

    service = StudioService(home=WORKSPACE, data_root=WORKSPACE / "data", model_factory=BrowserModel)
    service.set_interface_language("pt-BR")
    (WORKSPACE / "calls.jsonl").write_text("")
    existing = {voice["name"] for voice in service.read()["voices"]}
    for name in ("Narrator reference", "Ana reference", "Bruno reference"):
        if name not in existing:
            reference = service.upload_path(name + ".wav")
            sf.write(reference, np.ones(48000, dtype=np.float32) * 0.1, 24000)
            service.save_voice(name, reference, "Reference recording")
    register_pages(service)
    ui.run(host="127.0.0.1", port=17863, show=False, reload=False, language="pt-BR")


async def check_browser():
    from playwright.async_api import async_playwright, expect

    from f5_tts.studio.i18n import INTERFACE_LANGUAGES, translate

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    with (WORKSPACE / "server.log").open("w") as log:
        server = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--server"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        for _ in range(180):
            try:
                with urlopen(URL, timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError((WORKSPACE / "server.log").read_text())
                await asyncio.sleep(1)
        else:
            raise RuntimeError("Browser-test server did not start.")

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(args=["--no-sandbox"])
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(URL)

            async def language(locale):
                await page.locator("header").get_by_role("combobox").click()
                await page.get_by_role("option", name=INTERFACE_LANGUAGES[locale], exact=True).click()
                await expect(page.locator("html")).to_have_attribute("lang", locale, timeout=15000)

            for locale in INTERFACE_LANGUAGES:
                if locale != "pt-BR":
                    await language(locale)
                await expect(
                    page.get_by_text(translate("Transforme texto em voz.", locale), exact=True)
                ).to_be_visible()
                await expect(
                    page.get_by_role("button", name=translate("Histórias e personagens", locale), exact=True)
                ).to_be_visible()
                await expect(
                    page.get_by_role("button", name=translate("Gravar pelo microfone", locale), exact=True)
                ).to_be_visible()
                print("PASS language:", locale, flush=True)

            await language("en-US")
            await page.goto(URL + "/stories")
            title = "História original — " + uuid4().hex[:8]
            await page.get_by_label("Story name", exact=True).fill(title)
            await page.get_by_label("Character script", exact=True).fill(
                "Narrator: It was midnight.\nAna: Hello!\nBruno: We made it."
            )
            await page.get_by_role("button", name="Save story and assign dialogue", exact=True).click()
            await expect(page.get_by_text("Story and cast saved.", exact=True)).to_be_visible()
            await page.get_by_role("row").filter(has_text="Hello!").locator('[role="checkbox"], [role="radio"]').click()
            await expect(page.get_by_label("Selected dialogue text", exact=True)).to_have_value("Hello!")
            await page.get_by_label("Selected dialogue text", exact=True).fill("Hello, everyone!")
            await page.get_by_role("button", name="Save dialogue", exact=True).click()
            await expect(page.get_by_label("Character script", exact=True)).to_have_value(
                re.compile(r"Ana: Hello, everyone!")
            )
            await page.get_by_role("button", name="Generate complete story", exact=True).click()
            await expect(page.get_by_role("button", name="Download story WAV", exact=True)).to_be_visible(timeout=30000)
            for label, filename in [
                ("Download story WAV", "story.wav"),
                ("Download SRT subtitles", "story.srt"),
                ("Download script TXT", "story.txt"),
            ]:
                async with page.expect_download() as download_event:
                    await page.get_by_role("button", name=label, exact=True).click()
                download = await download_event.value
                await download.save_as(WORKSPACE / filename)
                assert (WORKSPACE / filename).stat().st_size > 0
            assert "Ana: Hello, everyone!" in (WORKSPACE / "story.srt").read_text()
            calls = [json.loads(line) for line in (WORKSPACE / "calls.jsonl").read_text().splitlines()]
            assert len(calls) == 3 and len({call["voice"] for call in calls}) == 3
            print(
                "PASS story: named cast, dialogue editing, three voices, generation and WAV/SRT/TXT downloads.",
                flush=True,
            )

            await language("ja")
            await page.get_by_label(translate("Abrir uma história", "ja"), exact=True).click()
            await page.get_by_role("option", name=title, exact=True).click()
            await page.get_by_role("button", name=translate("Abrir história", "ja"), exact=True).click()
            await expect(page.get_by_label(translate("Nome da história", "ja"), exact=True)).to_have_value(title)
            await expect(page.get_by_label(translate("Roteiro com personagens", "ja"), exact=True)).to_have_value(
                "Narrator: It was midnight.\n\nAna: Hello, everyone!\n\nBruno: We made it."
            )
            await page.get_by_role("button", name=translate("Gerar história completa", "ja"), exact=True).click()
            await expect(
                page.get_by_role("button", name=translate("Baixar história WAV", "ja"), exact=True)
            ).to_be_visible()
            assert len((WORKSPACE / "calls.jsonl").read_text().splitlines()) == 3
            assert not errors, errors
            await page.screenshot(path="validacao-historias.png", full_page=True)
            await browser.close()
            print("PASS persistence: changing UI language preserves story text, cast, and generated clips.", flush=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


if __name__ == "__main__":
    if "--server" in sys.argv:
        serve()
    else:
        asyncio.run(check_browser())
