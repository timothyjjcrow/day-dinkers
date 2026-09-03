from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
CSS = (ROOT / "public" / "styles-v15.css").read_text()


def hydration_source():
    start = APP.index("function hydrateChatImages")
    end = APP.index("function addPhotoToComposer", start)
    return APP[start:end]


def test_failed_chat_images_remain_visible_and_recoverable():
    hydration = hydration_source()
    assert "slot.remove();" not in hydration
    assert "slot.dataset.loaded = 'error'" in hydration
    assert "Photo couldn’t load." in hydration
    assert 'class="chat-image-retry">Retry</button>' in hydration
    assert "delete slot.dataset.loaded" in hydration
    assert "hydrateChatImages(msgsEl, chatUX);" in hydration


def test_every_chat_uses_the_branded_loading_placeholder():
    assert "</div>` : ''}\n" in APP
    assert ">⏳</div>" not in APP
    assert APP.count('class="chat-image-loading"') >= 5
    assert ".chat-image-loading.chat-image-error" in CSS
    assert ".chat-image-retry" in CSS
