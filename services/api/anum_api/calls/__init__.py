"""Phone calls (docs/calls.md).

Three parts, each usable on its own:

1. **Free, no provider.** Contacts, the call log and notes. A call is placed by the phone's
   own dialer (a ``tel:`` link); on return the person logs it and the workspace model can
   summarise the notes into a follow-up task.
2. **Calls through a voice provider** (Twilio Voice, off until a workspace owner stores the
   account's credentials). Calls to real phone numbers from the browser, recording and
   transcription only after an explicit consent step and a spoken notice, webhooks checked
   by signature.
3. **AI calls** on the provider: only to a contact with a recorded consent, each call bound
   to an owner's approval of the exact number, purpose and script, with an AI disclosure
   at the start, time and cost caps, opt-out honoured, and the transcript and summary
   logged. The AI may also answer the provider number and take a message.
"""
