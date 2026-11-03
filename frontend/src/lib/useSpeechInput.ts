"use client";

/**
 * useSpeechInput — dictation into a text box via the browser's Web Speech API.
 *
 * ── Keeps listening through pauses ──────────────────────────────────────────
 * Each recognition session ends on its own about a second after the speaker
 * pauses. Rather than `continuous = true` — which on Android Chrome re-sends
 * earlier phrases inside later results, so words come out duplicated — every
 * session is a single utterance and is restarted when it ends, until the user
 * presses stop or has been silent for SILENCE_MS. Text from finished sessions
 * is committed, so nothing said earlier is lost on restart.
 *
 * ── Never overwrites what was typed ─────────────────────────────────────────
 * Whatever is already in the box when listening starts is kept as a prefix;
 * speech is appended after it. Interim (still-being-recognised) words are shown
 * live and replaced as recognition firms them up.
 *
 * Chrome and Edge send the audio to their speech service, so this needs a
 * network connection; Firefox has no Web Speech API at all ("unsupported").
 */

import { useCallback, useEffect, useRef, useState } from "react";

/** Stop listening after this long with no new words. */
const SILENCE_MS = 4000;

export type SpeechInputError = "unsupported" | "no-speech" | "not-allowed" | "network" | "failed";

type RecognitionResult = { 0: { transcript: string } };
type Recognition = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((e: { results: ArrayLike<RecognitionResult> }) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};
type RecognitionCtor = new () => Recognition;

const join = (a: string, b: string) => [a.trim(), b.trim()].filter(Boolean).join(" ");

export function useSpeechInput({
  lang,
  onText,
  onError,
}: {
  /** BCP-47 language to recognise, e.g. "en-US" or "si-LK". */
  lang: string;
  /** Called with the full box text (typed prefix + speech so far). */
  onText: (text: string) => void;
  onError: (error: SpeechInputError) => void;
}) {
  const [listening, setListening] = useState(false);
  const recRef = useRef<Recognition | null>(null);
  const wantRef = useRef(false);          // user still wants to listen
  const committedRef = useRef("");        // prefix + text of finished sessions
  const sessionRef = useRef("");          // text of the current session
  const lastSpeechRef = useRef(0);
  const heardRef = useRef(false);         // any words at all this time
  const restartedRef = useRef(false);     // past the first session
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Latest callbacks without restarting recognition when they change.
  const onTextRef = useRef(onText);
  const onErrorRef = useRef(onError);
  useEffect(() => {
    onTextRef.current = onText;
    onErrorRef.current = onError;
  }, [onText, onError]);

  const finish = useCallback(() => {
    wantRef.current = false;
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
    setListening(false);
  }, []);

  const stop = useCallback(() => {
    wantRef.current = false;
    recRef.current?.stop();
  }, []);

  const start = useCallback((currentText: string) => {
    const w = window as unknown as Record<string, unknown>;
    const Ctor = (w.SpeechRecognition || w.webkitSpeechRecognition) as RecognitionCtor | undefined;
    if (!Ctor) {
      onErrorRef.current("unsupported");
      return;
    }

    const rec = new Ctor();
    rec.lang = lang;
    rec.interimResults = true;
    rec.continuous = false;
    recRef.current = rec;

    wantRef.current = true;
    heardRef.current = false;
    restartedRef.current = false;
    committedRef.current = currentText;
    sessionRef.current = "";
    lastSpeechRef.current = Date.now();

    rec.onresult = (e) => {
      sessionRef.current = Array.from(e.results, (r) => r[0].transcript).join("");
      if (sessionRef.current.trim()) {
        heardRef.current = true;
        lastSpeechRef.current = Date.now();
      }
      onTextRef.current(join(committedRef.current, sessionRef.current));
    };

    rec.onerror = (e) => {
      // "no-speech" just means this session heard nothing; the silence rule
      // decides whether to give up. "aborted" is our own stop().
      if (e.error === "no-speech" || e.error === "aborted") return;
      wantRef.current = false;
      // Safari may refuse an automatic restart (no fresh tap). The mic did
      // work, so end quietly with what was captured instead of blaming it.
      if (restartedRef.current && e.error === "not-allowed") return;
      onErrorRef.current(
        e.error === "not-allowed" || e.error === "service-not-allowed" || e.error === "audio-capture"
          ? "not-allowed"
          : e.error === "network"
            ? "network"
            : "failed",
      );
    };

    rec.onend = () => {
      committedRef.current = join(committedRef.current, sessionRef.current);
      sessionRef.current = "";
      const silentFor = Date.now() - lastSpeechRef.current;
      if (wantRef.current && silentFor < SILENCE_MS) {
        try {
          restartedRef.current = true;
          rec.start();
          return;
        } catch {
          // Fall through and finish if the browser refuses to restart.
        }
      }
      if (!heardRef.current && wantRef.current) onErrorRef.current("no-speech");
      finish();
    };

    // A session that stays open while nobody speaks is closed by this check.
    timerRef.current = setInterval(() => {
      if (wantRef.current && Date.now() - lastSpeechRef.current >= SILENCE_MS) rec.stop();
    }, 500);

    setListening(true);
    try {
      rec.start();
    } catch {
      onErrorRef.current("failed");
      finish();
    }
  }, [lang, finish]);

  // Release the microphone if the page unmounts mid-dictation.
  useEffect(() => () => {
    wantRef.current = false;
    if (timerRef.current) clearInterval(timerRef.current);
    recRef.current?.abort();
  }, []);

  return { listening, start, stop };
}
