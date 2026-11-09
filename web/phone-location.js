"use strict";
const button = document.getElementById("send");
const form = document.getElementById("location-form");
const fields = {
    location_type: document.getElementById("location-type"),
    location_name: document.getElementById("location-name"),
    location_detail: document.getElementById("location-detail"),
};
function lockInputs(disabled) {
    for (const field of Object.values(fields)) field.disabled = disabled;
}
const status = document.getElementById("status");
const coordinates = document.getElementById("coordinates");
const latitudeCell = document.getElementById("latitude");
const longitudeCell = document.getElementById("longitude");
const permissionHelp = document.getElementById("permission-help");
const tokens = new URL(window.location.href).searchParams.getAll("token");
const uncertain =
    "Could not confirm receipt on the PC.\nIt may already have received your location.\nCheck the PC before restarting and opening a new QR code.";
let destination;
if (tokens.length !== 1 || tokens[0] === "") {
    status.textContent =
        "Open this page again using the QR code displayed on your PC.";
} else if (!window.isSecureContext || !navigator.geolocation) {
    status.textContent =
        "Location requires a secure connection and a browser that supports location access.\nUse the HTTPS QR code from your PC.";
} else {
    destination = new URL("/location", window.location.origin);
    destination.searchParams.set("token", tokens[0]);
    button.disabled = false;
}

function locationFailure(error) {
    const messages = {
        1: "Safari or iOS blocked location access.\nNo location was sent.\nCheck both permissions below, then try again.",
        2: "Your location is unavailable.\nTry again.",
        3: "Location acquisition timed out.\nTry again.",
    };
    const denied =
        error.code === 1 ||
        error.name === "SecurityError" ||
        error.name === "NotAllowedError";
    permissionHelp.hidden = !denied;
    status.textContent = denied
        ? messages[1]
        : messages[error.code] ||
          "Could not start location acquisition.\nNothing was sent.\nTry again.";
    if (error.code === 1) status.textContent += "\n(PERMISSION_DENIED, code 1)";
    button.textContent = "Try Location Again";
    button.disabled = false;
    lockInputs(false);
}

async function acquired(position, metadata) {
    const latitude = position.coords.latitude;
    const longitude = position.coords.longitude;
    if (
        !Number.isFinite(latitude) ||
        !Number.isFinite(longitude) ||
        latitude < -90 ||
        latitude > 90 ||
        longitude < -180 ||
        longitude > 180
    ) {
        status.textContent =
            "Invalid coordinates received.\nNothing was sent.\nTry again.";
        button.disabled = false;
        lockInputs(false);
        return;
    }
    latitudeCell.textContent = String(latitude);
    longitudeCell.textContent = String(longitude);
    coordinates.hidden = false;
    status.textContent = "Sending location...";
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
        const response = await fetch(destination.href, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                latitude,
                longitude,
                sent_at: new Date().toISOString(),
                ...metadata,
            }),
            signal: controller.signal,
        });
        if (response.status !== 200) {
            if (response.status === 403) {
                status.textContent =
                    "This receiving URL is invalid.\nRestart on the PC and open the new QR code.";
            } else if (response.status === 409) {
                status.textContent =
                    "A location was already accepted, or receiving has ended.\nCheck the PC.";
            } else if (response.status >= 500) {
                status.textContent = uncertain;
            } else {
                status.textContent =
                    "The PC did not accept this request.\nCheck the PC before restarting with a new QR code.";
            }
            return;
        }
        const data = await response.json();
        if (
            controller.signal.aborted ||
            data === null ||
            data.status !== "ok"
        ) {
            status.textContent = uncertain;
            return;
        }
        status.textContent =
            "The PC received your location.\nCheck measurement results on the PC.\nStay in the same place until it finishes.";
    } catch (error) {
        status.textContent = uncertain;
    } finally {
        clearTimeout(timer);
    }
}

form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (button.disabled) return;
    const metadata = {};
    for (const [key, field] of Object.entries(fields)) {
        const value =
            key === "location_type" ? field.value : field.value.trim();
        const valid =
            key === "location_type"
                ? ["inside", "outside"].includes(value)
                : value.length > 0 &&
                  !value.includes("\0") &&
                  Array.from(value).length <=
                      (key === "location_name" ? 200 : 500);
        if (!valid) {
            const label =
                key === "location_name"
                    ? "Building or Landmark"
                    : "Location Details";
            const message =
                key === "location_type"
                    ? "Select Inside or Outside."
                    : value.length === 0
                      ? `Enter ${label}.`
                      : value.includes("\0")
                        ? `Remove invalid characters from ${label}.`
                        : `${label}: use up to ${key === "location_name" ? 200 : 500} characters.`;
            status.textContent = message;
            status.className = "input-error";
            return;
        }
        metadata[key] = value;
    }
    button.disabled = true;
    status.className = "";
    lockInputs(true);
    permissionHelp.hidden = true;
    button.textContent = "Get Location and Send";
    coordinates.hidden = true;
    latitudeCell.textContent = "";
    longitudeCell.textContent = "";
    status.textContent = "Acquiring location...";
    try {
        navigator.geolocation.getCurrentPosition(
            (position) => acquired(position, metadata),
            locationFailure,
            {
                enableHighAccuracy: true,
                maximumAge: 0,
                timeout: 30000,
            },
        );
    } catch (error) {
        locationFailure(error);
    }
});
