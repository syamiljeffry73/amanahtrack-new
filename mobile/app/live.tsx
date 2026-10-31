import { useEffect, useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { useRouter } from 'expo-router';

import { api, DeviceStatus, LiveEvent } from '../src/api/client';
import { timeAgo } from '../src/format';
import { useLive } from '../src/live';
import { colors, fonts, radius } from '../src/theme';
import { AuthImage } from '../src/ui/AuthImage';
import { Icon, IconName } from '../src/ui/Icon';
import { FadeUp, PressScale, Pulse } from '../src/ui/motion';
import { SEVERITY } from '../src/ui/severity';

const TITLE: Record<string, string> = {
  door_opened: 'Door opened',
  door_closed: 'Door closed',
  door_left_open: 'Door left open',
  vibration: 'Vibration detected',
  device_offline: 'Box went offline',
  unexplained_weight: 'Unexplained weight',
  tray_emptied: 'Tray emptied',
};

const ICON: Record<string, IconName> = {
  door_opened: 'box',
  door_closed: 'lock',
  door_left_open: 'alert',
  vibration: 'pulse',
  device_offline: 'alert',
  unexplained_weight: 'alert',
  tray_emptied: 'box',
};

/** Same parse rule as format.ts: a bare timestamp from the API is UTC. */
function ms(iso: string | null): number | null {
  if (!iso) return null;
  return new Date(/(Z|[+-]\d\d:?\d\d)$/.test(iso) ? iso : `${iso}Z`).getTime();
}

function clock(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export default function LiveScreen() {
  const { connected, events, devices } = useLive();
  const router = useRouter();

  // Re-render every second so "last seen" and the open timer keep moving.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <View style={s.screen}>
      <ScrollView contentContainerStyle={s.content}>
        <FadeUp>
          <View style={s.headRow}>
            <View>
              <Text style={s.title}>Live box feed</Text>
              <Text style={s.sub}>Door, vibration and camera, as they happen</Text>
            </View>
            <View style={[s.conn, !connected && s.connOff]}>
              {connected ? (
                <Pulse>
                  <View style={[s.dot, { backgroundColor: colors.green }]} />
                </Pulse>
              ) : (
                <View style={[s.dot, { backgroundColor: colors.brass }]} />
              )}
              <Text style={[s.connText, !connected && { color: colors.brass }]}>
                {connected ? 'Live' : 'Reconnecting'}
              </Text>
            </View>
          </View>
        </FadeUp>

        {devices.length === 0 ? (
          <FadeUp delay={80}>
            <View style={s.card}>
              <Text style={s.cardMuted}>No box has reported yet.</Text>
            </View>
          </FadeUp>
        ) : (
          devices.map((d, i) => (
            <FadeUp key={d.serial} delay={80 + i * 60}>
              <DeviceCard device={d} />
            </FadeUp>
          ))
        )}

        <FadeUp delay={160}>
          <Text style={s.section}>ACTIVITY</Text>
        </FadeUp>

        {events.length === 0 ? (
          <View style={s.empty}>
            <View style={s.emptyIcon}>
              <Icon name="pulse" size={24} color={colors.green} />
            </View>
            <Text style={s.emptyTitle}>Nothing yet</Text>
            <Text style={s.emptyBody}>
              Open the box door or tap the box and the event appears here within a second.
            </Text>
          </View>
        ) : (
          events.map((e, i) => (
            <FadeUp key={e.id} delay={i < 8 ? i * 40 : 0} distance={10} duration={380}>
              <EventRow event={e} onOpen={(id) => router.push(`/alert/${id}`)} />
            </FadeUp>
          ))
        )}
      </ScrollView>
    </View>
  );
}

function DeviceCard({ device: d }: { device: DeviceStatus }) {
  const seen = ms(d.last_seen_at);
  const age = seen ? Math.max(0, Math.round((Date.now() - seen) / 1000)) : null;
  // Judge online from the clock, not from the flag in the last message, so a
  // box that went silent turns offline here even if no message says so.
  const online = age !== null && age <= 90;
  const open = d.door_state === 'open';
  const openedAt = ms(d.door_opened_at);
  const openFor = open && openedAt ? Math.max(0, Math.round((Date.now() - openedAt) / 1000)) : null;

  return (
    <View style={s.card}>
      <View style={s.deviceTop}>
        <View style={{ flex: 1 }}>
          <Text style={s.deviceName}>{d.label}</Text>
          <Text style={s.deviceMeta}>
            {d.serial}
            {age !== null ? `  ·  seen ${age < 60 ? `${age} s` : `${Math.round(age / 60)} min`} ago` : ''}
          </Text>
        </View>
        <View style={[s.statePill, { backgroundColor: online ? colors.greenSoft : colors.dangerSoft }]}>
          <View style={[s.dot, { backgroundColor: online ? colors.green : colors.danger }]} />
          <Text style={[s.stateText, { color: online ? colors.green : colors.danger }]}>
            {online ? 'Online' : 'Offline'}
          </Text>
        </View>
      </View>

      <View style={[s.door, open ? s.doorOpen : s.doorShut]}>
        <Icon
          name={open ? 'alert' : 'lock'}
          size={20}
          color={open ? colors.brass : colors.green}
        />
        <View style={{ flex: 1 }}>
          <Text style={[s.doorTitle, { color: open ? colors.brass : colors.ink }]}>
            {d.door_state === null ? 'Door state unknown' : open ? 'Door open' : 'Door closed'}
          </Text>
          {openFor !== null && <Text style={s.doorSub}>Open for {clock(openFor)}</Text>}
        </View>
      </View>
    </View>
  );
}

function EventRow({ event: e, onOpen }: { event: LiveEvent; onOpen: (alertId: string) => void }) {
  const sev = e.severity ? SEVERITY[e.severity] : null;
  const tint = sev?.tint ?? colors.inkFaint;
  const tappable = !!e.alert_id;
  return (
    <PressScale
      disabled={!tappable}
      scaleTo={0.985}
      onPress={() => e.alert_id && onOpen(e.alert_id)}
      style={[s.row, sev && { borderColor: tint + '44' }]}
    >
      <View style={[s.rowIcon, { backgroundColor: sev?.soft ?? colors.surfaceSunk }]}>
        <Icon name={ICON[e.kind] ?? 'pulse'} size={19} color={tint} />
      </View>
      <View style={{ flex: 1 }}>
        <View style={s.rowTop}>
          <Text style={s.rowTitle}>{TITLE[e.kind] ?? e.kind}</Text>
          <Text style={s.rowTime}>{timeAgo(e.occurred_at)}</Text>
        </View>
        {!!e.detail && (
          <Text style={s.rowDetail} numberOfLines={2}>
            {e.detail}
          </Text>
        )}
        {tappable && <Text style={s.rowLink}>Review alert</Text>}
      </View>
      {e.has_photo && e.alert_id && (
        <AuthImage alertId={e.alert_id} style={s.thumb} resizeMode="cover" />
      )}
    </PressScale>
  );
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.paper },
  content: { padding: 20, paddingBottom: 60, gap: 12 },

  headRow: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between' },
  title: { fontFamily: fonts.display, fontSize: 22, letterSpacing: -0.5, color: colors.ink },
  sub: { fontFamily: fonts.body, fontSize: 12.5, color: colors.inkFaint, marginTop: 4 },
  conn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    paddingVertical: 6,
    paddingHorizontal: 11,
    borderRadius: 999,
    backgroundColor: colors.greenSoft,
  },
  connOff: { backgroundColor: colors.brassSoft },
  connText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.green },
  dot: { width: 8, height: 8, borderRadius: 4 },

  section: {
    fontFamily: fonts.bodyBold,
    fontSize: 10.5,
    letterSpacing: 1.3,
    color: colors.inkFaint,
    marginTop: 10,
  },

  card: {
    padding: 14,
    borderRadius: radius.lg,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    gap: 12,
  },
  cardMuted: { fontFamily: fonts.body, fontSize: 13, color: colors.inkFaint },
  deviceTop: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  deviceName: { fontFamily: fonts.bodySemi, fontSize: 15, color: colors.ink },
  deviceMeta: { fontFamily: fonts.body, fontSize: 12, color: colors.inkFaint, marginTop: 2 },
  statePill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingVertical: 5,
    paddingHorizontal: 10,
    borderRadius: 999,
  },
  stateText: { fontFamily: fonts.bodyBold, fontSize: 12 },
  door: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 12,
    borderRadius: radius.md,
    borderWidth: 1,
  },
  doorShut: { backgroundColor: colors.surfaceSunk, borderColor: colors.line },
  doorOpen: { backgroundColor: colors.brassSoft, borderColor: 'rgba(240,198,116,0.4)' },
  doorTitle: { fontFamily: fonts.bodySemi, fontSize: 14.5 },
  doorSub: { fontFamily: fonts.body, fontSize: 12, color: colors.inkSoft, marginTop: 2 },

  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 12,
    borderRadius: radius.lg,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  rowIcon: { width: 40, height: 40, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  rowTop: { flexDirection: 'row', justifyContent: 'space-between', gap: 8 },
  rowTitle: { fontFamily: fonts.bodySemi, fontSize: 14, color: colors.ink },
  rowTime: { fontFamily: fonts.body, fontSize: 11.5, color: colors.inkFaint },
  rowDetail: { fontFamily: fonts.body, fontSize: 12.5, color: colors.inkSoft, marginTop: 2 },
  rowLink: { fontFamily: fonts.bodyBold, fontSize: 11.5, color: colors.green, marginTop: 5 },
  thumb: { width: 56, height: 56, borderRadius: 10, backgroundColor: colors.surfaceSunk },

  empty: { alignItems: 'center', paddingTop: 36, gap: 8 },
  emptyIcon: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: colors.greenSoft,
    alignItems: 'center',
    justifyContent: 'center',
  },
  emptyTitle: { fontFamily: fonts.display, fontSize: 16, color: colors.ink },
  emptyBody: {
    fontFamily: fonts.body,
    fontSize: 13,
    color: colors.inkFaint,
    textAlign: 'center',
    maxWidth: 270,
  },
});
