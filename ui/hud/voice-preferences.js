import { VOICE_STORAGE_KEYS } from "./config.js";

const VALID_GENDERS = new Set(["auto", "male", "female"]);
const VALID_LANGUAGES = new Set(["auto", "english", "urdu", "hindi", "roman_urdu"]);

function readStorage(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch (_) { return fallback; }
}

export class VoicePreferences {
  constructor() {
    this.gender = VALID_GENDERS.has(readStorage(VOICE_STORAGE_KEYS.gender, "auto"))
      ? readStorage(VOICE_STORAGE_KEYS.gender, "auto") : "auto";
    this.language = this.#startupLanguage() || (
      VALID_LANGUAGES.has(readStorage(VOICE_STORAGE_KEYS.language, "auto"))
        ? readStorage(VOICE_STORAGE_KEYS.language, "auto")
        : "auto"
    );
    this.lastInputLanguage = "english_or_mixed";
  }

  #startupLanguage() {
    try {
      const requested = new URLSearchParams(window.location.search).get("lang") || "";
      if (/^(?:ur|urdu)$/i.test(requested)) return "urdu";
      if (/^(?:en|english)$/i.test(requested)) return "english";
    } catch (_) { /* Older browsers may not expose URLSearchParams. */ }
    return null;
  }

  save() {
    try {
      localStorage.setItem(VOICE_STORAGE_KEYS.gender, this.gender);
      localStorage.setItem(VOICE_STORAGE_KEYS.language, this.language);
    } catch (_) { /* Private browsing may disable localStorage. */ }
  }

  setGender(value) {
    if (VALID_GENDERS.has(value)) this.gender = value;
  }

  setLanguage(value) {
    if (VALID_LANGUAGES.has(value)) this.language = value;
  }

  genderLabel(value = this.gender) {
    return ({ auto: "AUTO", male: "MALE", female: "FEMALE" }[value] || "AUTO");
  }

  languageLabel(value = this.language) {
    return ({ auto: "AUTO", english: "ENGLISH", urdu: "URDU", hindi: "HINDI", roman_urdu: "ROMAN URDU" }[value] || "AUTO");
  }

  displayLabel() {
    return `VOICE ${this.languageLabel()} • ${this.genderLabel()}`;
  }

  detectLanguageHint(text) {
    const value = String(text || "");
    if (/[\u0600-\u06FF]/.test(value)) return "urdu_script";
    if (/[\u0900-\u097F]/.test(value)) return "hindi_script";
    if (/\b(?:mujhe|muje|aap|apko|tumhe|meri|mere|yaad|yad|bhool|bhul|kya|kia|hai|nahi|nhi|karo|krdo|acha|accha)\b/i.test(value)) {
      return "roman_urdu_or_hinglish";
    }
    return "english_or_mixed";
  }

  setLastInputLanguage(value) {
    this.lastInputLanguage = value || "english_or_mixed";
  }

  autoRecognitionLanguage() {
    const locales = Array.isArray(navigator.languages) && navigator.languages.length
      ? navigator.languages
      : [navigator.language || "en-US"];
    return /^ur(?:-|$)/i.test(locales[0]) ? "ur-PK" : "en-US";
  }

  recognitionLanguage() {
    if (this.language === "urdu") return "ur-PK";
    if (this.language === "english") return "en-US";
    if (this.lastInputLanguage === "urdu_script") return "ur-PK";
    return this.autoRecognitionLanguage();
  }

  outputLanguageDetails(text) {
    let language = this.language;
    if (language === "auto") {
      language = /[\u0600-\u06FF]/.test(text) ? "urdu" : (/[\u0900-\u097F]/.test(text) ? "hindi" : "english");
    }
    if (language === "urdu") return { language, speechLang: "ur-PK", voiceLang: "ur" };
    if (language === "hindi") return { language, speechLang: "hi-IN", voiceLang: "hi" };
    return { language, speechLang: "en-US", voiceLang: "en" };
  }
}
