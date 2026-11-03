import { useEffect } from 'react';
import { Platform } from 'react-native';
import Constants, { ExecutionEnvironment } from 'expo-constants';
import { useRouter } from 'expo-router';

import { api } from './api/client';

/**
 * Push notifications.
 *
 * The box raises an alert, the backend sends it to Expo's push service, and
 * the phone shows it even if the app is closed. Tapping it opens that alert.
 *
 * This only works in a development build (or a store build). Expo Go no longer
 * supports remote push on Android, so there it quietly does nothing and the
 * in-app live feed still works as before.
 */

const inExpoGo = Constants.executionEnvironment === ExecutionEnvironment.StoreClient;

let currentToken: string | null = null;

function notifications() {
  // Loaded on demand so Expo Go never touches the module.
  return require('expo-notifications') as typeof import('expo-notifications');
}

async function registerForPush(): Promise<string | null> {
  if (inExpoGo || Platform.OS !== 'android') return null;
  const Device = require('expo-device') as typeof import('expo-device');
  if (!Device.isDevice) return null;

  const N = notifications();
  N.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: true,
      shouldSetBadge: false,
    }),
  });

  await N.setNotificationChannelAsync('alerts', {
    name: 'Box alerts',
    importance: N.AndroidImportance.MAX,
    vibrationPattern: [0, 250, 250, 250],
    sound: 'default',
  });

  let { status } = await N.getPermissionsAsync();
  if (status !== 'granted') {
    status = (await N.requestPermissionsAsync()).status;
  }
  if (status !== 'granted') return null;

  const projectId =
    Constants.expoConfig?.extra?.eas?.projectId ?? Constants.easConfig?.projectId;
  if (!projectId) return null;

  const token = (await N.getExpoPushTokenAsync({ projectId })).data;
  await api.registerPushToken(token);
  currentToken = token;
  return token;
}

/** Called on sign out so this phone stops getting this person's alerts. */
export async function unregisterPush(): Promise<void> {
  if (!currentToken) return;
  const token = currentToken;
  currentToken = null;
  try {
    await api.removePushToken(token);
  } catch {
    // Signing out must never fail because of a notification clean up.
  }
}

/** Mount once inside the signed in part of the app. */
export function usePushSetup(signedIn: boolean) {
  const router = useRouter();

  useEffect(() => {
    if (!signedIn) return;
    registerForPush().catch(() => {});
  }, [signedIn]);

  useEffect(() => {
    if (inExpoGo || Platform.OS !== 'android') return;
    const N = notifications();
    const open = (response: import('expo-notifications').NotificationResponse | null) => {
      const id = response?.notification.request.content.data?.alert_id;
      if (typeof id === 'string') router.push(`/alert/${id}`);
    };
    const sub = N.addNotificationResponseReceivedListener(open);
    // The app was closed and the tap is what started it.
    open(N.getLastNotificationResponse?.() ?? null);
    return () => sub.remove();
  }, [router]);
}
