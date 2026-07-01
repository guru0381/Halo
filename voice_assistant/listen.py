"""Always-on wake word + record + transcribe.

Heavy deps (openwakeword, sounddevice, faster_whisper, numpy) are imported
lazily inside the functions so that `main.py --text ...` works on a machine
where only the brain/actions stack is installed.
"""

import time

from . import config

_whisper = None  # cached faster-whisper model


def _get_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel

        print(f"→ Loading Whisper ({config.WHISPER_MODEL})…")
        _whisper = WhisperModel(
            config.WHISPER_MODEL, device="cpu", compute_type=config.WHISPER_COMPUTE
        )
    return _whisper


def transcribe(audio_int16):
    """audio_int16: 1-D int16 numpy array at 16 kHz. Returns the recognized text."""
    import numpy as np

    audio_f32 = audio_int16.astype(np.float32) / 32768.0
    # vad_filter drops non-speech, which kills Whisper's "You"/"Thank you"
    # hallucinations on near-silent clips.
    segments, _ = _get_whisper().transcribe(
        audio_f32, language="en", beam_size=1, vad_filter=True
    )
    return " ".join(seg.text for seg in segments).strip()


def _rms(frame_int16):
    import numpy as np

    if len(frame_int16) == 0:
        return 0.0
    return float(np.sqrt(np.mean(frame_int16.astype(np.float32) ** 2)))


def record_until_silence(stream):
    """Capture one spoken utterance from an open input stream.

    Waits (up to SPEECH_ONSET_TIMEOUT) for speech to actually start, trims the
    leading silence, then keeps recording until SILENCE_SECONDS of quiet follows
    the speech. Returns an empty array if the speaker never says anything — so a
    pause after the wake word yields "nothing heard", not a silence hallucination.
    """
    import numpy as np

    frames = []
    started = time.time()
    last_voice = None              # set on the first voiced frame
    while True:
        block, _ = stream.read(config.FRAME_SAMPLES)
        frame = np.frombuffer(block, dtype=np.int16)
        now = time.time()
        if _rms(frame) >= config.SILENCE_RMS:
            last_voice = now
        if last_voice is not None:
            frames.append(frame)   # only keep audio once speech has started
            if now - last_voice >= config.SILENCE_SECONDS:
                break              # a pause after real speech ends the utterance
        elif now - started >= config.SPEECH_ONSET_TIMEOUT:
            break                  # nobody started speaking — give up
        if now - started >= config.RECORD_MAX_SECONDS:
            break
    return np.concatenate(frames) if frames else np.array([], dtype=np.int16)


# --- pluggable wake-word backends -------------------------------------------
# Each backend exposes: .frame_length (samples per read), .label (for prompts),
# .process(int16_frame) -> bool, .reset(), .close().


class _OpenWakeWord:
    """Built-in pretrained phrases (hey_jarvis, alexa, …)."""

    def __init__(self):
        from openwakeword.model import Model

        print(f"→ Loading openWakeWord model ({config.WAKEWORD_MODEL})…")
        self.frame_length = config.FRAME_SAMPLES
        self.label = config.WAKEWORD_MODEL.replace("_", " ")
        self._oww = Model(wakeword_models=[config.WAKEWORD_MODEL])

    def process(self, frame):
        scores = self._oww.predict(frame)
        return any(s >= config.WAKEWORD_THRESHOLD for s in scores.values())

    def reset(self):
        self._oww.reset()

    def close(self):
        pass


class _Porcupine:
    """Custom phrase ("Hey Guru") from a Picovoice .ppn keyword file."""

    def __init__(self):
        import os

        import pvporcupine

        if not config.PORCUPINE_KEY:
            raise RuntimeError(
                "Porcupine needs a free access key. Get one at console.picovoice.ai "
                "and set ASSISTANT_PORCUPINE_KEY."
            )
        if not os.path.exists(config.PORCUPINE_KEYWORD_PATH):
            raise RuntimeError(
                f"Keyword file not found: {config.PORCUPINE_KEYWORD_PATH}. "
                "Create a 'Hey Guru' keyword at console.picovoice.ai, download the "
                "macOS (.ppn) file, and put it there (or set ASSISTANT_PORCUPINE_KEYWORD)."
            )
        print("→ Loading Porcupine keyword (Hey Guru)…")
        self._pp = pvporcupine.create(
            access_key=config.PORCUPINE_KEY,
            keyword_paths=[config.PORCUPINE_KEYWORD_PATH],
        )
        self.frame_length = self._pp.frame_length        # Porcupine fixes this (512)
        self.label = "hey guru"

    def process(self, frame):
        return self._pp.process(frame) >= 0

    def reset(self):
        pass

    def close(self):
        self._pp.delete()


def _normalize(s):
    import re

    # Lowercase, turn any run of non-alphanumerics (punctuation, repeated spaces)
    # into a single space, and trim — so "Hey, Guru!" -> "hey guru".
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


class _WhisperWake:
    """Account-free custom phrase: transcribe a rolling window and match a phrase.

    Heavier (runs Whisper periodically on the mic) and a touch laggier than
    openWakeWord / Porcupine, but needs no model download, no key, and no
    training — it reuses the Whisper you already have.
    """

    def __init__(self):
        import collections

        self.frame_length = config.FRAME_SAMPLES                 # 1280 (80 ms)
        self.label = config.WAKE_PHRASE
        self._phrase = _normalize(config.WAKE_PHRASE).strip()
        per = config.SAMPLE_RATE / self.frame_length             # frames per second
        self._window_frames = max(1, int(config.WAKE_WINDOW_SECONDS * per))
        self._check_every = max(1, int(config.WAKE_CHECK_SECONDS * per))
        self._buf = collections.deque(maxlen=self._window_frames)
        self._since = 0
        print(f'→ Whisper phrase-spotting for "{config.WAKE_PHRASE}" '
              f"(window {config.WAKE_WINDOW_SECONDS}s, every ~{config.WAKE_CHECK_SECONDS}s)…")
        _get_whisper()                                           # preload model

    def process(self, frame):
        import numpy as np

        self._buf.append(frame)
        self._since += 1
        if self._since < self._check_every or len(self._buf) < self._window_frames:
            return False
        self._since = 0
        window = np.concatenate(list(self._buf))
        if _rms(window) < config.SILENCE_RMS:    # no voice -> skip (saves CPU, avoids hallucinations)
            return False
        heard = _normalize(transcribe(window))
        return bool(self._phrase) and self._phrase in heard

    def reset(self):
        self._buf.clear()
        self._since = 0

    def close(self):
        pass


def make_wakeword():
    """Construct the wake-word backend selected by config.WAKEWORD_ENGINE."""
    if config.WAKEWORD_ENGINE == "porcupine":
        return _Porcupine()
    if config.WAKEWORD_ENGINE == "whisper":
        return _WhisperWake()
    return _OpenWakeWord()


def wake_word_loop(on_command):
    """Block forever: on each wake word, record -> transcribe -> on_command(text, listen_again).

    `listen_again` is a callback the handler can invoke to capture one more
    spoken utterance from the same mic stream (used for "send / cancel"
    confirmation on the email flow).
    """
    import numpy as np
    import sounddevice as sd

    ww = make_wakeword()
    # Both engines run at 16 kHz mono int16; only the frame size differs.
    with sd.RawInputStream(
        samplerate=config.SAMPLE_RATE,
        blocksize=ww.frame_length,
        dtype="int16",
        channels=1,
    ) as stream:
        def listen_again():
            audio = record_until_silence(stream)
            return transcribe(audio) if len(audio) else ""

        print(f'✅ Listening ({config.WAKEWORD_ENGINE}). Say "{ww.label}"…')
        try:
            while True:
                block, _ = stream.read(ww.frame_length)
                frame = np.frombuffer(block, dtype=np.int16)
                if ww.process(frame):
                    print("🔔 Wake word! Listening for your command…")
                    ww.reset()
                    audio = record_until_silence(stream)
                    if len(audio) == 0:
                        continue
                    text = transcribe(audio)
                    if text:
                        print(f'   heard: "{text}"')
                        on_command(text, listen_again)
                    else:
                        print("   (didn't catch anything)")
        finally:
            ww.close()
