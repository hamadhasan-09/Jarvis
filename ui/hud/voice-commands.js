export function parseMicrophoneControl(text) {
  const value = String(text || "").trim();
  const micMention = /\b(?:mic|microphone|voice recognition|speech recognition|listening|voice|awaz|awaaz|sunna)\b|مائیک|مائک|مائکروفون|آواز|سننا|سنو/i.test(value);
  const mute = /\b(?:mute|disable|turn off|switch off|shut off|stop listening|don't listen|do not listen|be quiet|silence)\b|\b(?:off|stop|band|band\s+(?:kar|kardo|krdo))\s+(?:the\s+)?(?:mic|microphone|voice|awaz|awaaz)\b|\b(?:mic|microphone|voice|awaz|awaaz)\s+(?:off|band|band\s+(?:kar|kardo|krdo))\b|(?:مائیک|مائک|مائکروفون|آواز)(?:\s+کو)?\s*(?:بند|خاموش)|(?:سننا|سنو)(?:\s+کو)?\s*(?:بند|خاموش)/i.test(value);
  const unmute = /\b(?:unmute|enable|turn on|switch on|start listening|resume listening|listen again)\b|\b(?:on|start|chalu|chaalu)\s+(?:the\s+)?(?:mic|microphone|voice|awaz|awaaz)\b|\b(?:mic|microphone|voice|awaz|awaaz)\s+(?:on|chalu|chaalu)\b|(?:مائیک|مائک|مائکروفون|آواز)(?:\s+کو)?\s*(?:آن|چالو|کھولو)|(?:دوبارہ|پھر)\s*(?:سنو|سننا)/i.test(value);
  if (!micMention && !/\b(?:mute|unmute|silence|quiet)\b/i.test(value)) return null;
  if (unmute) return "unmute";
  if (mute) return "mute";
  return null;
}

export function parseVoiceControl(text) {
  const value = String(text || "").trim();
  const lowered = value.toLowerCase();
  const femalePattern = /\b(?:female|woman|lady|girl|aurat|khatoon|khawateen|ladki|mahila)\b|स्त्री|महिला|औरत|خاتون|خواتین|عورت/i;
  const malePattern = /\b(?:male|man|gentleman|boy|mard|larka|ladka|mardana)\b|पुरुष|आदमी|लड़का|مرد|مردانہ|لڑکا/i;
  const genderMentions = value.match(/\b(?:female|woman|lady|girl|aurat|khatoon|khawateen|ladki|mahila|male|man|gentleman|boy|mard|larka|ladka|mardana)\b|स्त्री|महिला|औरत|خاتون|خواتین|عورت|पुरुष|आदमी|लड़का|مرد|مردانہ|لڑکا/gi) || [];
  const lastGenderMention = genderMentions[genderMentions.length - 1] || "";
  const female = femalePattern.test(value);
  const male = malePattern.test(value);
  const requestedGender = femalePattern.test(lastGenderMention) ? "female" : (malePattern.test(lastGenderMention) ? "male" : null);
  const languagePattern = /\b(?:urdu|pakistani urdu|hindi|roman urdu|roman|english)\b|اردو|हिन्दी|हिंदी|رومن اردو|انگریزی|अंग्रेज़ी|अंग्रेजी/gi;
  const languageMentions = value.match(languagePattern) || [];
  const lastLanguageMention = languageMentions[languageMentions.length - 1] || "";
  const language = /roman|رومن اردو/i.test(lastLanguageMention) ? "roman_urdu"
    : /urdu|اردو/i.test(lastLanguageMention) ? "urdu"
    : /hindi|हिन्दी|हिंदी/i.test(lastLanguageMention) ? "hindi"
    : /english|انگریزی|अंग्रेज़ी|अंग्रेजी/i.test(lastLanguageMention) ? "english" : null;
  const resetGender = /\b(reset|auto(?:matic)?|default|natural)\s+(?:voice|gender)\b|\bvoice\s+(?:back\s+)?to\s+(?:default|normal|automatic)\b/i.test(lowered);
  const resetLanguage = /\b(reset|auto(?:matic)?|default)\s+(?:language|lang)\b|\b(language|zaban|زبان|भाषा)\s+(?:back\s+)?to\s+(?:default|normal|automatic)\b/i.test(lowered);
  const controlWord = /\b(?:switch|change|use|set|select|choose|make|speak|talk|reply|answer|respond|voice|gender|language|lang|zaban|awaz)\b|آواز|زبان|भाषा|आवाज़|बोलो|बोलें|بات کرو/i.test(value);
  const shortVoiceCommand = /^(?:female|male|woman|man|urdu|اردو|hindi|हिन्दी|हिंदी|english|انگریزی|अंग्रेज़ी|अंग्रेजी|roman urdu|auto(?:matic)?|default)\s*(?:voice|language|lang|gender)?[.!?]*$/i.test(value);
  if (!controlWord && !shortVoiceCommand) return null;
  if (!female && !male && !language && !resetGender && !resetLanguage) return null;
  return {
    gender: requestedGender || (resetGender ? "auto" : null),
    language: language || (resetLanguage ? "auto" : null),
  };
}

export function voiceConfirmation(preferences, control, lastInputLanguage) {
  const language = preferences.language !== "auto"
    ? preferences.language
    : (control.language && control.language !== "auto" ? control.language : (lastInputLanguage === "urdu_script" ? "urdu" : (lastInputLanguage === "hindi_script" ? "hindi" : "english")));
  const genderText = preferences.genderLabel();
  const languageText = preferences.languageLabel();
  if (language === "urdu") return `ٹھیک ہے، اب آواز ${genderText === "FEMALE" ? "خواتین" : genderText === "MALE" ? "مردانہ" : "خودکار"} ہے اور زبان ${languageText} ہے۔`;
  if (language === "hindi") return `ठीक है, अब आवाज़ ${genderText === "FEMALE" ? "महिला" : genderText === "MALE" ? "पुरुष" : "ऑटो"} है और भाषा ${languageText} है।`;
  if (language === "roman_urdu") return `Theek hai, ab voice ${genderText.toLowerCase()} hai aur language ${languageText} hai.`;
  return `Voice updated to ${genderText.toLowerCase()} and ${languageText.toLowerCase()}.`;
}
