# Real push notifications (Android)

The box raises an alert, the backend sends it to Expo's push service, and the phone shows it even when the app is closed. Tapping it opens that alert.

This needs a development build. Expo Go cannot receive remote push on Android, so after this you open the app from its own icon instead of Expo Go.

## Part 1. Update the backend (laptop)
Unzip push-update.zip into C:\amanahtrack (overwrite). It contains backend, mobile and PUSH_SETUP.md folders and files, not a new folder called amanahtrack. Restart uvicorn.
The new table is created by itself on start.

## Part 2. Add the packages (laptop)
```
cd C:\amanahtrack\mobile
npx expo install expo-notifications expo-device expo-dev-client
```

## Part 3. Firebase (browser, one time)
1. Go to console.firebase.google.com, sign in with a Google account, Add project (name: amanahtrack, turn Analytics off).
2. Project overview, click the Android icon to add an Android app.
   Package name must be exactly: my.amanahtrack.app
   Click Register app, then Download google-services.json.
3. Save that file as C:\amanahtrack\mobile\google-services.json (keep it private: add the line `google-services.json` to C:\amanahtrack\.gitignore so it is never uploaded to GitHub).
4. Project settings (gear), Service accounts tab, Generate new private key. Save the JSON somewhere safe on your laptop, outside the project folder. This is used once in Part 5.

## Part 4. Expo account and the build
```
npm install -g eas-cli
eas login
eas init
eas build --profile development --platform android
```
`eas init` fills in the project id for you. The build runs in the cloud and takes 10 to 20 minutes. When it finishes it prints a link and a QR code. Open it on your phone, download the APK and install it (allow installing from this source if Android asks).

## Part 5. Give Expo the Firebase key
```
eas credentials
```
Choose Android, then the development profile, then Google Service Account, Manage your Google Service Account Key for Push Notifications (FCM V1), then Upload a new key and pick the private key JSON from Part 3 step 4.

## Part 6. Run and test
```
cd C:\amanahtrack\mobile
$env:EXPO_PUBLIC_API_URL="http://<laptop IP>:8000"
npx expo start --dev-client -c
```
Open the AmanahTrack app on the phone (the new icon), scan or pick the server, log in, and allow notifications when asked.
Swipe the app away completely, then open the box door. A notification should appear. Tap it to open the alert.
