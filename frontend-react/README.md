# Third Shot React App

Mobile-first React shell for Third Shot, built with Vite and prepared for Capacitor packaging.

## Web workflow

- `npm install --legacy-peer-deps`
- `npm run dev`
- `npm test`
- `npm run build`

## Mobile environment

Native builds need explicit backend URLs because a packaged Capacitor webview cannot use relative `/api` routes.

Copy `.env.mobile.example` to a real env file and set:

- `VITE_API_BASE_URL`
- `VITE_PUBLIC_APP_URL`

Optional for live reload:

- `CAP_SERVER_URL`

## Capacitor workflow

- `npm run cap:add:ios`
- `npm run cap:add:android`
- `npm run cap:sync`
- `npm run cap:open:ios`
- `npm run cap:open:android`

## Native readiness in this repo

- Capacitor config lives in `capacitor.config.ts`
- Native-safe runtime helpers live in `src/lib/runtime.ts` and `src/lib/native.ts`
- iOS location permission copy is set in `ios/App/App/Info.plist`
- Android location permissions are set in `android/app/src/main/AndroidManifest.xml`

## Current machine limitations

This workspace can generate and sync the Capacitor shells, but iOS and Android do not have identical local requirements:

- iOS: full Xcode from the App Store is still required for `xcodebuild`, `pod install`, and device/simulator builds.
- Android: verified working on this machine with JDK 17 plus Android SDK platform 34 and build-tools 34.0.0.

## Verified Android build setup

- `CocoaPods 1.16.2`
- `OpenJDK 17.0.18`
- `Android SDK Platform 34`
- `Android SDK Build-Tools 34.0.0`
- `Android SDK Platform-Tools`

Build command used successfully:

- `JAVA_HOME="$(/usr/libexec/java_home -v 17)" ANDROID_SDK_ROOT=/opt/homebrew/share/android-commandlinetools ANDROID_HOME=/opt/homebrew/share/android-commandlinetools ./gradlew assembleDebug`

Produced APK:

- `android/app/build/outputs/apk/debug/app-debug.apk`
