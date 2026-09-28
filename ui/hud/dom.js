const REQUIRED_IDS = Object.freeze([
  "coreScene",
  "voicePermissionGate",
  "voicePermissionCopy",
  "voicePermissionButton",
  "approvalModal",
  "approvalAction",
  "approvalRisk",
  "approvalTarget",
  "approvalPrompt",
  "approvalApproveButton",
  "approvalDenyButton",
  "approvalCancelButton",
  "statusLabel",
  "statusDetail",
  "jarvisLive",
  "userLive",
  "voiceModule",
  "taskState",
  "connectionLabel",
]);

export function createDomRegistry(documentRef = document) {
  const nodes = Object.fromEntries(
    REQUIRED_IDS.map((id) => [id, documentRef.getElementById(id)]),
  );
  const missing = REQUIRED_IDS.filter((id) => !nodes[id]);
  if (missing.length) {
    throw new Error(`JARVIS HUD is missing required elements: ${missing.join(", ")}`);
  }
  return Object.freeze(nodes);
}
