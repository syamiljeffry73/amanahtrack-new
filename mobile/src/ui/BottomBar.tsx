import { StyleSheet, Text, View } from 'react-native';
import { useRouter } from 'expo-router';

import { canWrite } from '../api/client';
import { useSession } from '../session';
import { colors, fonts } from '../theme';
import { Icon, IconName } from './Icon';
import { PressScale } from './motion';

type Tab = { key: string; label: string; icon: IconName; href: string; write?: boolean };

const TABS: Tab[] = [
  { key: 'home', label: 'Home', icon: 'home', href: '/' },
  { key: 'spend', label: 'Spend', icon: 'spend', href: '/spend', write: true },
  { key: 'live', label: 'Live', icon: 'pulse', href: '/live' },
  { key: 'alerts', label: 'Alerts', icon: 'bell', href: '/alerts' },
  { key: 'ledger', label: 'Ledger', icon: 'ledger', href: '/ledger' },
];

/** Floating tab bar. Roles that cannot write only see Home, Alerts and Ledger. */
export function BottomBar({ active, alertCount = 0 }: { active: string; alertCount?: number }) {
  const router = useRouter();
  const { session } = useSession();
  const writable = canWrite(session?.role);
  const tabs = TABS.filter((t) => !t.write || writable);

  return (
    <View style={s.wrap} pointerEvents="box-none">
      <View style={s.bar}>
        {tabs.map((t) => {
          const on = t.key === active;
          return (
            <PressScale
              key={t.key}
              style={[s.item, on && s.itemOn]}
              onPress={() => (on ? undefined : router.push(t.href as never))}
              accessibilityRole="button"
              accessibilityLabel={t.label}
            >
              <View>
                <Icon name={t.icon} size={22} color={on ? colors.green : colors.inkFaint} />
                {t.key === 'alerts' && alertCount > 0 && (
                  <View style={s.badge}>
                    <Text style={s.badgeText}>{alertCount > 9 ? '9+' : alertCount}</Text>
                  </View>
                )}
              </View>
              <Text style={[s.label, on && s.labelOn]}>{t.label}</Text>
            </PressScale>
          );
        })}
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  wrap: { position: 'absolute', left: 20, right: 20, bottom: 20 },
  bar: {
    flexDirection: 'row',
    gap: 4,
    padding: 6,
    borderRadius: 22,
    backgroundColor: colors.surfaceRaised,
    borderWidth: 1,
    borderColor: colors.lineStrong,
  },
  item: {
    flex: 1,
    height: 52,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 2,
  },
  badge: {
    position: 'absolute',
    top: -5,
    right: -9,
    minWidth: 16,
    height: 16,
    paddingHorizontal: 4,
    borderRadius: 8,
    backgroundColor: colors.danger,
    alignItems: 'center',
    justifyContent: 'center',
  },
  badgeText: { fontFamily: fonts.bodyBold, fontSize: 10, color: colors.paper },
  itemOn: { backgroundColor: colors.greenSoft },
  label: { fontFamily: fonts.bodySemi, fontSize: 10.5, color: colors.inkFaint },
  labelOn: { fontFamily: fonts.bodyBold, color: colors.green },
});
