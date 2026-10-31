import { useEffect, useState } from 'react';
import { Image, ImageStyle, StyleProp } from 'react-native';

import { api } from '../api/client';

const cache = new Map<string, string>();

/** An alert photo that needs the login token. Downloads it, then shows it. */
export function AuthImage({
  alertId,
  style,
  resizeMode = 'cover',
  onError,
}: {
  alertId: string;
  style?: StyleProp<ImageStyle>;
  resizeMode?: 'cover' | 'contain';
  onError?: () => void;
}) {
  const [uri, setUri] = useState<string | null>(cache.get(alertId) ?? null);

  useEffect(() => {
    if (cache.has(alertId)) {
      setUri(cache.get(alertId)!);
      return;
    }
    let alive = true;
    api
      .photoDataUri(alertId)
      .then((data) => {
        cache.set(alertId, data);
        if (alive) setUri(data);
      })
      .catch(() => alive && onError?.());
    return () => {
      alive = false;
    };
  }, [alertId]);

  if (!uri) return null;
  return <Image source={{ uri }} style={style} resizeMode={resizeMode} onError={onError} />;
}
