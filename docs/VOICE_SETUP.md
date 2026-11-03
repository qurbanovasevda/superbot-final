# Voice input bonus

## Install

Replace `app/ui.py` with the supplied version and restart `python run.py`.
No Python dependency, speech API key, paid speech integration or model change is
required. Keep the existing Gemini configuration. Open http://127.0.0.1:8000
and refresh the page (Ctrl+F5 if needed).

## Use

Voice input prepares an editable draft. It never sends a chat message automatically.

1. Choose Speech language: English, Azerbaijani or Russian.
2. Click Start microphone and grant browser microphone permission.
3. Speak a short request, then pause or click Stop microphone. Recognized text
   appears in the input field. Do not hold a button.
4. Review or edit the text, then press Send (or Enter). Only then does it enter
   the existing chat/routing pipeline. Recognition stops once a draft is ready.
5. Click Start microphone again to record another draft after sending or clearing
   the current draft. Explicit Stop preserves the latest recognized words for review.
6. Optionally enable Read replies aloud. Stop playback interrupts the spoken reply.
   A matching installed browser/OS voice is required; otherwise text stays visible.

Typing stops microphone capture to avoid replacing a typed draft. Starting voice
with an existing draft asks you to send or clear it first. Hidden tabs, page exit
and New conversation stop voice mode. Voice mode does not start automatically
on page load. Audio is not recorded or stored by the SuperChat backend.

## Availability and privacy

The implementation uses browser SpeechRecognition/webkitSpeechRecognition and
SpeechSynthesis APIs. Feature availability, recognition languages, permissions
and installed playback voices vary by browser and operating system. The UI
checks recognition support, but cannot guarantee Azerbaijani recognition or an
Azerbaijani playback voice on every machine. Unsupported languages, denied
permissions, microphone failures and repeated network failures stop recognition
with a message. Text chat remains available.

Speech recognition may send audio to the browser provider's online service;
this is not a guaranteed offline solution. Only transcripts enter the existing
SuperChat chat endpoint. Spoken chat turns use the same configured Gemini quota
as typed turns. There is no separate paid speech API configured in this build.
For deployed use, microphone access generally needs a secure context; localhost
is used for this Phase 1 demo. Browser silence segmentation is implementation
controlled, so actual end-of-speech timing needs local testing.

## Acceptance checklist

- Start microphone once, speak two separate searches with a pause between them:
  each should create a draft, without holding a button. No chat request should
  occur until you explicitly press Send.
- Try `Wireless headphones under 100 AZN` using English recognition.
- Choose Azerbaijani before starting and try `100 manatdan ucuz qulaqlıq göstər`.
  Check the transcript, not just the model's answer. If recognition rejects the
  language, use another supported language or type; do not claim AZ audio works.
- Choose Russian and try `Покажи ноутбуки`.
- Enable Read replies aloud: verify the microphone does not transcribe the app's
  own voice. Stop playback, then start the microphone for another draft.
- Say a flight reservation request: the chat must ask confirmation. No booking
  should be committed until you explicitly confirm. A spoken confirmation is also a draft; review it and press Send to confirm.
- Stop microphone while a partial transcript is visible: it should become an
  editable draft and must not be sent.
- Deny microphone permission: a clear error should appear and typing must work.
- Switch tabs or start a new conversation: microphone mode should turn off.

## Development verification

143 Python tests pass (one existing non-failing deprecation warning).
Embedded JavaScript passes Node syntax validation. Run
`node tests/voice_ui.test.cjs` for twelve mocked lifecycle checks covering final vs
interim results, buffering, single draft preparation, pause/resume, service restart,
permission denial, network failure limits and oversized input. These checks do
not verify a real microphone, browser recognition service or speech playback.
Hardware/browser acceptance remains open until the checklist is executed.

Reference: https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition
Reference: https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesis
