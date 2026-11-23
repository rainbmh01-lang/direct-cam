# direct-cam — DirectCam Mobile Camera & Microphone Streamer

> Client / Owner: Internal  
> Category: apps  
> Status: active  
> Primary Stack: Capacitor 6, Android, Vanilla HTML5/CSS3/JavaScript, Python 3 (PC Receiver)

## One Job
Streams Android phone camera (front/back) and microphone directly to a Windows PC over USB (`adb reverse`) or Wi-Fi with zero setup and a strictly flat, no-header, monochrome interface.

## Context & Inputs
- **Codebase Root**: `./`
- **Configuration**: `package.json`, `capacitor.config.json`
- **Shared Reference**: `../../../_shared/rules.md`

## Architecture & Workflows
- **Entry Points**: `www/index.html`, `www/app.js`, `pc-receiver/receiver.py`
- **Key Modules**:
  - `www/`: Flat utilitarian mobile camera/microphone capture and WebSocket binary transmitter.
  - `android/`: Capacitor Android native wrapper with camera, microphone, and cleartext traffic permissions.
  - `pc-receiver/`: Lightweight Python WebSocket/HTTP receiver with automatic `adb reverse` and Virtual Camera/Audio routing.

## Development & Execution Commands
```bash
# Sync web assets to Android project
npx cap sync android

# Build APK via Gradle
cd android && ./gradlew assembleDebug

# Run PC receiver
python pc-receiver/receiver.py
```

## Outputs & Deliverables
- **Build Artifacts**: `DirectCam-v1.0.apk` (`android/app/build/outputs/apk/debug/app-debug.apk`)
- **Production Target**: Android APK + Local Windows PC Receiver

## Human Gate & Verification
Verify camera permission prompt, front/back camera switching, microphone streaming, and strict flat monochrome UI (no headers, no border-radius, no emojis).
