import { HUD_CONFIG } from "./config.js";
import { createDomRegistry } from "./dom.js";
import { LifecycleManager } from "./lifecycle.js";
import { AccessibilityController } from "./accessibility.js";
import { ProceduralCoreRenderer } from "./core-renderer.js";
import { HudState } from "./state.js";
import { VoicePreferences } from "./voice-preferences.js";
import { TtsInterface } from "./tts.js";
import { AudioInterface } from "./audio.js";
import { SocketEventBridge } from "./socket-bridge.js";
import { VoiceInterface } from "./voice.js";
import { ConversationController } from "./conversation.js";
import { ApprovalController } from "./approval.js";

export function createHudApp(documentRef = document) {
  const dom = createDomRegistry(documentRef);
  const lifecycle = new LifecycleManager();

  const coreCanvas = documentRef.getElementById("coreCanvas");

  if (!coreCanvas) {
    throw new Error("JARVIS coreCanvas element is missing from ui/index.html.");
  }

  const renderer = new ProceduralCoreRenderer({
    canvas: coreCanvas,
  });

  const hudState = new HudState({
    dom,
    renderer,
  });

  const preferences = new VoicePreferences();
  const tts = new TtsInterface({
    preferences,
  });

  const audio = new AudioInterface({
    renderer,
    enabled: HUD_CONFIG.audioReactiveEnabled,
  });

  const socket = new SocketEventBridge(HUD_CONFIG.socket);

  const accessibility = new AccessibilityController({
    modal: dom.approvalModal,
    focusTarget: dom.approvalApproveButton,
  });

  lifecycle.add(
    tts.on("start", () => {
      hudState.setJarvisSpeaking(true);
      hudState.setBackendState("speaking");
      hudState.setStatus("RESPONDING", "JARVIS is speaking...");
    }),
  );

  lifecycle.add(
    tts.on("end", () => {
      hudState.setJarvisSpeaking(false);
      hudState.setBackendState("listening");
      hudState.setStatus("LISTENING", "Ready for your command...");
    }),
  );

  lifecycle.add(
    tts.on("error", (event) => {
      hudState.setJarvisSpeaking(false);
      hudState.setBackendState("error");

      hudState.setStatus(
        "AUDIO OUTPUT ERROR",
        event && event.error === "unsupported"
          ? "This browser does not provide speech output."
          : "Text reply received, but the browser could not speak it.",
      );
    }),
  );

  let conversation;
  let voice;

  voice = new VoiceInterface({
    dom,
    preferences,
    tts,
    audio,
    lifecycle,

    onUserSpeech: (text) => {
      if (conversation) {
        conversation.receiveUserSpeech(text);
      }
    },

    onInterrupt: (heardText, wasSpeaking) => {
      const replyCancelled = conversation && conversation.cancelActiveReply(heardText);
      if (wasSpeaking || replyCancelled) tts.cancel();
    },

    onFinalTranscript: (text) => {
      if (conversation) {
        conversation.receiveFinalTranscript(text);
      }
    },

    onEnded: () => {
      if (conversation) {
        conversation.flushSpeechBuffer();
      }
    },

    onStatus: (label, detail, state) => {
      if (state) {
        hudState.setBackendState(state);
      }

      hudState.setStatus(label, detail);
    },
  });

  conversation = new ConversationController({
    dom,
    socket,
    hudState,
    preferences,
    tts,
    voice,
    lifecycle,
  });

  const approval = new ApprovalController({
    dom,
    socket,
    hudState,
    accessibility,
    tts,
    lifecycle,
  });

  let stateTimeoutId = null;

  const setTerminalState = (
    state,
    task,
    label,
    detail,
    reset = true,
  ) => {
    clearTimeout(stateTimeoutId);

    hudState.setBackendState(state);
    hudState.setTask(task);
    hudState.setStatus(label, detail);

    if (reset) {
      stateTimeoutId = window.setTimeout(() => {
        hudState.setBackendState("idle");
        hudState.setTask("IDLE");
        hudState.setStatus(
          "STANDBY",
          "Ready for your command...",
        );
      }, HUD_CONFIG.terminalStateResetMs);
    }
  };

  const handleStatus = (data = {}) => {
    const requestId = data.request_id
      ? String(data.request_id)
      : null;

    if (
      requestId &&
      !conversation.acceptsEvent(requestId)
    ) {
      return;
    }

    const state = String(
      data.state || "",
    ).toLowerCase();

    if (
      ["completed", "cancelled", "failed", "error"].includes(
        state,
      )
    ) {
      approval.close();
    }

    if (state === "retrieving") {
      hudState.setBackendState(state);
      hudState.setTask("RETRIEVING");
      hudState.setStatus(
        "RETRIEVING",
        "Checking the local knowledge base...",
      );
    } else if (state === "verifying") {
      hudState.setBackendState(state);
      hudState.setTask("VERIFYING");
      hudState.setStatus(
        "VERIFYING",
        "Running the configured answer check...",
      );
    } else if (state === "thinking") {
      hudState.setBackendState(state);
      hudState.setTask("THINKING");
      hudState.setStatus(
        "THINKING",
        "Understanding your request...",
      );
    } else if (state === "generating") {
      hudState.setBackendState(state);
      hudState.setTask("GENERATING");
      hudState.setStatus(
        "THINKING",
        "Generating response...",
      );
    } else if (state === "executing") {
      hudState.setBackendState(state);
      hudState.setTask("EXECUTING");
      hudState.setStatus(
        "EXECUTING",
        data.message || "Running autonomous engine...",
      );
    } else if (state === "waiting_confirmation") {
      hudState.setBackendState(state);
      hudState.setTask("AUTHORIZATION");
      hudState.setStatus(
        "AUTHORIZATION",
        data.message || "Waiting for user approval...",
      );
    } else if (state === "approved") {
      hudState.setBackendState(state);
      hudState.setTask("APPROVED");
      hudState.setStatus(
        "APPROVED",
        data.message || "Action approved.",
      );
    } else if (state === "completed") {
      setTerminalState(
        "completed",
        "COMPLETED",
        "COMPLETED",
        data.message || "Action completed.",
      );
    } else if (state === "cancelled") {
      setTerminalState(
        "cancelled",
        "CANCELLED",
        "CANCELLED",
        data.message || "Action cancelled.",
      );
    } else if (state === "failed") {
      hudState.setBackendState(state);
      hudState.setTask("FAILED");
      hudState.setStatus(
        "FAILED",
        data.message || "Action failed.",
      );
    } else if (state === "error") {
      hudState.setBackendState(state);
      hudState.setTask("ERROR");
      hudState.setStatus(
        "ERROR",
        data.message || "Error occurred.",
      );
    }
  };

  const bindEvents = () => {
    socket.on("connect", () => {
      dom.connectionLabel.textContent = "ONLINE";
      voice.start(false);
    });

    socket.on("disconnect", () => {
      dom.connectionLabel.textContent = "RECONNECTING";

      hudState.setBackendState("error");

      hudState.setStatus(
        "RECONNECTING",
        "Restoring live command channel...",
      );
    });

    socket.on("system_status", () => {});

    socket.on("status", handleStatus);

    socket.on("approval_request", (data) => {
      approval.show(data);
    });

    socket.on("jarvis_reply_token", (data) => {
      conversation.handleToken(data);
    });

    socket.on("jarvis_reply", (data) => {
      approval.close();
      conversation.handleReply(data);
    });
  };

  const start = () => {
    bindEvents();

    dom.voiceModule.textContent =
      preferences.displayLabel();

    lifecycle.listen(
      documentRef,
      "pointerdown",
      () => { tts.unlock(); void voice.start(true); },
      {
        once: true,
        passive: true,
      },
    );

    lifecycle.listen(
      documentRef,
      "keydown",
      () => { tts.unlock(); void voice.start(true); },
      {
        once: true,
      },
    );

    lifecycle.listen(
      dom.voicePermissionButton,
      "click",
      async () => {
        const button = dom.voicePermissionButton;

        button.disabled = true;
        button.textContent = "REQUESTING...";

        tts.unlock();

        const permitted =
          await voice.requestPermission(true);

        if (permitted) {
          await voice.start(true);
        }

        button.disabled = false;
        button.textContent = "TRY AGAIN";
      },
    );

    window.setTimeout(() => {
      voice.shouldListen = true;
      voice.start(false);
      tts.unlock();
    }, 0);
  };

  const destroy = () => {
    clearTimeout(stateTimeoutId);

    approval.destroy();
    conversation.destroy();
    voice.destroy();
    tts.destroy();
    audio.destroy();
    accessibility.destroy();
    hudState.destroy();
    socket.destroy();
    lifecycle.destroy();
  };

  return Object.freeze({
    start,
    destroy,
    dom,
    socket,
    hudState,
    voice,
    tts,
    conversation,
    approval,
  });
}