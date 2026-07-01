"""Voice-triggered personal assistant — reminder slice.

Pipeline:  wake word -> record -> Whisper (STT) -> Gemma 4 (intent) -> action.
This package ships the reminder vertical slice end-to-end; the email slice is
stubbed (classified but not yet wired) and lands in phase 2.
"""
