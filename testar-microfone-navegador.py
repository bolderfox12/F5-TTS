"""Teste de ponta a ponta com microfone simulado; usa somente o workspace de teste."""
import asyncio
import json
import wave
from uuid import uuid4
from pathlib import Path

from playwright.async_api import async_playwright, expect


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            args=["--no-sandbox", "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
        )
        context = await browser.new_context(permissions=["microphone"], viewport={"width": 1365, "height": 1000})
        await context.add_init_script("""(() => {
            const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
            navigator.mediaDevices.getUserMedia = async constraints => {
                window.microphoneTestStream = await original(constraints);
                return window.microphoneTestStream;
            };
        })();""")
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto("http://127.0.0.1:7863/voices")
        print("Página de vozes carregada.", flush=True)
        start = page.get_by_role("button", name="Gravar pelo microfone", exact=True)
        await start.click()
        await expect(page.get_by_role("button", name="Parar gravação", exact=True)).to_be_visible()
        await page.wait_for_timeout(1800)
        await page.get_by_role("button", name="Parar gravação", exact=True).click()
        await expect(page.locator("audio[src^='blob:']")).to_be_visible()
        print("Gravação e prévia disponíveis.", flush=True)
        assert await page.evaluate("microphoneTestStream.getTracks().every(track => track.readyState === 'ended')")
        await page.get_by_role("button", name="Usar esta gravação", exact=True).click()
        await expect(page.get_by_text("Gravação do microfone selecionada como referência.", exact=True)).to_be_visible(timeout=30000)
        await page.get_by_label("Nome da voz", exact=True).fill("Microfone simulado — teste")
        await page.get_by_label("Transcrição da referência (opcional)", exact=True).fill("Teste de microfone")
        consent = page.get_by_role("checkbox", name="Tenho autorização para usar esta gravação")
        await consent.click()
        await expect(consent).to_have_attribute("aria-checked", "true")
        await page.get_by_role("button", name="Salvar voz", exact=True).click()
        await expect(page.get_by_text("Voz salva.", exact=True)).to_be_visible()
        print("Gravação convertida, selecionada e salva como voz.", flush=True)
        await page.screenshot(path="validacao-microfone.png", full_page=True)

        # The fake permission UI always grants access; inject the standard denial
        # returned by the browser to test the application's error message.
        await page.evaluate("""() => {
            navigator.mediaDevices.getUserMedia = async () => {
                throw new DOMException('Permission denied', 'NotAllowedError');
            };
        }""")
        await start.click()
        await expect(page.get_by_text("Acesso ao microfone negado.", exact=False)).to_be_visible()
        print("Permissão negada tratada.", flush=True)
        checkpoint = Path("/tmp/f5-studio-microphone-test/test-model.pt")
        checkpoint.write_bytes(b"dummy checkpoint: registry UI test only, never loaded")
        await page.goto("http://127.0.0.1:7863/models")
        model_name = "Português — teste " + uuid4().hex[:8]
        await page.get_by_label("Nome do modelo", exact=True).fill(model_name)
        await page.get_by_label("Caminho do checkpoint", exact=True).fill(str(checkpoint))
        await page.get_by_role("button", name="Cadastrar modelo", exact=True).click()
        await expect(page.get_by_text(model_name, exact=True)).to_be_visible()
        await page.goto("http://127.0.0.1:7863/")
        selector = page.get_by_label("Modelo para gerar áudio", exact=True)
        await selector.click()
        await page.get_by_role("option", name=model_name, exact=True).click()
        await expect(page.locator(".q-select__selected-value").filter(has_text=model_name)).to_be_visible()
        await selector.click()
        await page.get_by_role("option", name="F5-TTS original · inglês / chinês", exact=True).click()
        await expect(page.locator(".q-select__selected-value").filter(has_text="F5-TTS original · inglês / chinês")).to_be_visible()
        assert errors == [], errors
        await browser.close()

    workspace = json.loads(Path("/tmp/f5-studio-microphone-test/workspace.json").read_text())
    voice = next(voice for voice in workspace["voices"] if voice["name"] == "Microfone simulado — teste")
    with wave.open(voice["reference"]) as audio:
        assert audio.getframerate() == 24000
        assert audio.getnchannels() == 1
        assert 1 <= audio.getnframes() / audio.getframerate() <= 30
    print("PASSOU: gravar, parar, ouvir prévia, liberar microfone, selecionar referência, salvar voz, tratar permissão negada, cadastrar modelo e alternar com o original.")


if __name__ == "__main__":
    asyncio.run(main())
