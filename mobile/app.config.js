// Adds google-services.json only when the file is present, so the app still
// starts in Expo Go before the Firebase file has been downloaded.
const fs = require('fs');

module.exports = ({ config }) => ({
  ...config,
  android: {
    ...config.android,
    ...(fs.existsSync('./google-services.json')
      ? { googleServicesFile: './google-services.json' }
      : {}),
  },
});
