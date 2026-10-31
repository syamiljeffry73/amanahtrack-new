import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { AppState } from 'react-native';

import {
  api,
  DeviceStatus,
  LiveEvent,
  LiveMessage,
  liveSocketUrl,
} from './api/client';
import { useSession } from './session';

/**
 * Live connection to the backend.
 *
 * One WebSocket for the whole app, opened when someone is signed in. It
 * reconnects with a growing delay if the network drops, and every time it
 * (re)connects it fetches what was missed, so the feed is complete even if the
 * phone was asleep. The socket is a shortcut for speed; the REST catch-up is
 * what keeps the feed correct.
 */

interface LiveState {
  connected: boolean;
  events: LiveEvent[];
  devices: DeviceStatus[];
  /** null until the server has told us, so Home keeps its own number meanwhile */
  openAlerts: number | null;
  criticalOpen: number;
}

const initial: LiveState = {
  connected: false,
  events: [],
  devices: [],
  openAlerts: null,
  criticalOpen: 0,
};

const LiveContext = createContext<LiveState>(initial);

export function useLive(): LiveState {
  return useContext(LiveContext);
}

const MAX_EVENTS = 100;

export function LiveProvider({ children }: { children: React.ReactNode }) {
  const { session } = useSession();
  const [state, setState] = useState<LiveState>(initial);
  const socketRef = useRef<WebSocket | null>(null);

  const catchUp = useCallback(async () => {
    try {
      const [events, devices] = await Promise.all([api.liveEvents(), api.devices()]);
      setState((s) => ({ ...s, events, devices }));
    } catch {
      // The next reconnect tries again; a failed catch-up is not fatal.
    }
  }, []);

  useEffect(() => {
    if (!session) {
      setState(initial);
      return;
    }

    let closed = false;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const handle = (msg: LiveMessage) => {
      if (msg.type === 'event') {
        setState((s) => ({
          ...s,
          events: [msg.event, ...s.events.filter((e) => e.id !== msg.event.id)].slice(0, MAX_EVENTS),
        }));
      } else if (msg.type === 'status') {
        setState((s) => ({
          ...s,
          devices: s.devices.some((d) => d.serial === msg.device.serial)
            ? s.devices.map((d) => (d.serial === msg.device.serial ? msg.device : d))
            : [...s.devices, msg.device],
        }));
      } else if (msg.type === 'alert_count') {
        setState((s) => ({ ...s, openAlerts: msg.open, criticalOpen: msg.critical_open }));
      }
    };

    const connect = () => {
      if (closed) return;
      const ws = new WebSocket(liveSocketUrl());
      socketRef.current = ws;

      ws.onopen = () => {
        retry = 0;
        setState((s) => ({ ...s, connected: true }));
        catchUp();
      };
      ws.onmessage = (e) => {
        try {
          handle(JSON.parse(String(e.data)) as LiveMessage);
        } catch {
          // ignore a malformed frame
        }
      };
      ws.onerror = () => {
        // onclose follows; reconnecting is handled there
      };
      ws.onclose = () => {
        setState((s) => ({ ...s, connected: false }));
        if (closed) return;
        const delay = Math.min(1000 * 2 ** retry, 15000);
        retry += 1;
        timer = setTimeout(connect, delay);
      };
    };

    connect();

    // Coming back to the app: do not wait out the backoff.
    const sub = AppState.addEventListener('change', (next) => {
      if (next !== 'active') return;
      const ws = socketRef.current;
      if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
        if (timer) clearTimeout(timer);
        retry = 0;
        connect();
      } else {
        catchUp();
      }
    });

    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      sub.remove();
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [session, catchUp]);

  const value = useMemo(() => state, [state]);
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}
