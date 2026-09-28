import { createHudApp } from "./app.js";

const app = createHudApp();
app.start();

// Expose only lifecycle/debug access. UI business logic remains inside modules.
globalThis.jarvisHud = app;
