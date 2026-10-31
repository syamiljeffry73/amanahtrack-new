import { useCallback, useEffect, useState } from 'react';
import {
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
  useWindowDimensions,
} from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';

import { api, canWrite, Fund } from '../src/api/client';
import { useLive } from '../src/live';
import { useSession } from '../src/session';
import { colors, fonts, radius, ringgit, space } from '../src/theme';
import { BottomBar } from '../src/ui/BottomBar';
import { Icon } from '../src/ui/Icon';
import { LogoMark } from '../src/ui/Logo';
import { FadeUp, GrowX, PressScale, Pulse, Skeleton } from '../src/ui/motion';

type Verification = { intact: boolean; checked: number; summary: string };

const ROLE_LABEL: Record<string, string> = {
  admin: 'Admin',
  treasurer: 'Treasurer',
  auditor: 'Auditor',
  committee: 'Committee',
};

/** Short plain-English rule line per fund category. */
const RULE_LINE: Record<string, string> = {
  sadaqah: 'Any lawful purpose',
  zakat: 'Eight asnaf only',
  infaq: 'Bound to donor intent',
  khairat: 'Funeral support only',
  waqf_principal: 'Perpetual, income only',
  waqf_income: 'Within the waqif’s purpose',
  nazar: 'Bound to the vow',
  fidyah: 'The poor only',
};

function fundColor(f: Fund): string {
  switch (f.category) {
    case 'waqf_principal':
    case 'waqf_income':
      return colors.fund.waqf;
    case 'zakat':
      return colors.fund.zakat;
    case 'sadaqah':
      return colors.fund.sadaqah;
    case 'infaq':
      return colors.fund.infaq;
    case 'khairat':
      return colors.fund.khairat;
    default:
      return colors.fund.other;
  }
}

export default function HomeScreen() {
  const router = useRouter();
  const { width } = useWindowDimensions();
  const { session, signOut } = useSession();
  const writable = canWrite(session?.role);
  const live = useLive();

  const [funds, setFunds] = useState<Fund[]>([]);
  const [verification, setVerification] = useState<Verification | null>(null);
  const [alertInfo, setAlertInfo] = useState({ open: 0, critical_open: 0 });
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A pushed alert count updates the badge and card without a pull to refresh.
  useEffect(() => {
    if (live.openAlerts !== null) {
      setAlertInfo({ open: live.openAlerts, critical_open: live.criticalOpen });
    }
  }, [live.openAlerts, live.criticalOpen]);

  const load = useCallback(async () => {
    try {
      const [f, v, a] = await Promise.all([api.funds(), api.verify(), api.alertSummary()]);
      setFunds(f);
      setVerification(v);
      setAlertInfo(a);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not reach the ledger');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const cash = funds.find((f) => f.code === 'cash');
  const rest = funds.filter((f) => f.code !== 'cash');
  const spendable = rest.filter((f) => f.spendable && f.category === 'sadaqah');
  const restricted = rest.filter((f) => f.spendable && f.category !== 'sadaqah');
  const locked = rest.filter((f) => !f.spendable);

  const total = rest.reduce((sum, f) => sum + parseFloat(f.balance), 0);
  const pending = cash ? parseFloat(cash.provisional) : 0;
  const barWidth = width - 40 - 36;

  return (
    <View style={s.screen}>
      <View style={s.glow} />
      <ScrollView
        contentContainerStyle={s.content}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => {
              setRefreshing(true);
              load();
            }}
            tintColor={colors.green}
          />
        }
      >
        <FadeUp>
          <View style={s.header}>
            <View style={s.headLeft}>
              <LogoMark size={38} />
              <View>
                <Text style={s.institution}>Masjid Al-Hidayah</Text>
                <Text style={s.role}>
                  {session?.name} {'·'} {ROLE_LABEL[session?.role ?? ''] ?? session?.role}
                </Text>
              </View>
            </View>
            <View style={s.headRight}>
              <PressScale
                onPress={() => router.push('/alerts')}
                style={s.iconBtn}
                accessibilityLabel="Alerts"
              >
                <Icon name="bell" size={19} color={colors.inkSoft} />
                {alertInfo.open > 0 && <View style={s.bellDot} />}
              </PressScale>
              <PressScale onPress={signOut} style={s.iconBtn} accessibilityLabel="Sign out">
                <Icon name="logout" size={19} color={colors.inkSoft} />
              </PressScale>
            </View>
          </View>
        </FadeUp>

        {error && (
          <View style={s.errorBar}>
            <Text style={s.errorText}>{error}</Text>
            <Text style={s.errorHint}>Pull down to try again.</Text>
          </View>
        )}

        {loading ? (
          <HomeSkeleton />
        ) : (
          <>
            {alertInfo.open > 0 && (
              <FadeUp delay={60}>
                <PressScale
                  onPress={() => router.push('/alerts')}
                  scaleTo={0.98}
                  style={[s.alertCard, alertInfo.critical_open > 0 && s.alertCardCritical]}
                >
                  <View style={s.alertIcon}>
                    <Icon
                      name="alert"
                      size={21}
                      color={alertInfo.critical_open > 0 ? colors.danger : colors.brass}
                    />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={s.alertTitle}>
                      {alertInfo.open} alert{alertInfo.open === 1 ? '' : 's'} need
                      {alertInfo.open === 1 ? 's' : ''} review
                    </Text>
                    <Text style={s.alertSub}>Check the photos and verify the hashes</Text>
                  </View>
                  <Icon name="chevron" size={18} color={colors.inkFaint} />
                </PressScale>
              </FadeUp>
            )}

            <FadeUp delay={100} style={s.hero}>
              <View style={s.heroTop}>
                <Text style={s.heroLabel}>TOTAL HELD</Text>
                {verification && (
                  <View style={[s.sealPill, !verification.intact && s.sealPillBroken]}>
                    <Icon
                      name="shield"
                      size={13}
                      color={verification.intact ? colors.green : colors.danger}
                      strokeWidth={2}
                    />
                    <Text style={[s.sealText, !verification.intact && { color: colors.danger }]}>
                      {verification.intact ? 'Ledger sealed' : 'Chain broken'}
                    </Text>
                  </View>
                )}
              </View>
              <Text style={s.heroAmount}>{ringgit(cash ? cash.balance : total)}</Text>

              <View style={s.bar}>
                {rest
                  .filter((f) => parseFloat(f.balance) > 0)
                  .map((f, i) => {
                    const w = Math.max(
                      4,
                      (parseFloat(f.balance) / Math.max(total, 1)) * barWidth - 3,
                    );
                    return (
                      <GrowX
                        key={f.id}
                        width={w}
                        delay={400 + i * 60}
                        style={{ width: w, height: 8, borderRadius: 4, backgroundColor: fundColor(f) }}
                      >
                        <View />
                      </GrowX>
                    );
                  })}
              </View>

              <View style={s.heroFoot}>
                <Text style={s.heroFootText}>
                  {verification ? `${verification.checked} entries verified` : ''}
                </Text>
                {pending > 0 && (
                  <View style={s.pendingRow}>
                    <Pulse>
                      <View style={s.pendingDot} />
                    </Pulse>
                    <Text style={s.pendingText}>{ringgit(pending)} pending count</Text>
                  </View>
                )}
              </View>
            </FadeUp>

            {pending > 0 && (
              <FadeUp delay={220}>
                <View style={s.boxCard}>
                  <View style={s.boxIcon}>
                    <Icon name="box" size={22} color={colors.brass} strokeWidth={1.7} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={s.boxTitle}>Donation box</Text>
                    <Text style={s.boxSub}>Estimates waiting for a witnessed count</Text>
                  </View>
                  {writable && (
                    <PressScale onPress={() => router.push('/count')} style={s.countBtn}>
                      <Text style={s.countBtnText}>Count</Text>
                    </PressScale>
                  )}
                </View>
              </FadeUp>
            )}

            <FadeUp delay={320} style={s.fundsHead}>
              <Text style={s.fundsTitle}>Funds</Text>
              <Text style={s.fundsSub}>Grouped by spending rule</Text>
            </FadeUp>

            <FadeUp delay={400}>
              <View style={s.list}>
                <Group label="SPENDABLE" funds={spendable} writable={writable} first />
                <Group label="RESTRICTED" funds={restricted} writable={writable} />
                <Group label="LOCKED" funds={locked} writable={false} />
              </View>
            </FadeUp>

            {!writable && (
              <Text style={s.readOnly}>
                Your account can view records but not post transactions.
              </Text>
            )}
          </>
        )}
      </ScrollView>

      <BottomBar active="home" alertCount={alertInfo.open} />
    </View>
  );
}

function Group({
  label,
  funds,
  writable,
  first,
}: {
  label: string;
  funds: Fund[];
  writable: boolean;
  first?: boolean;
}) {
  const router = useRouter();
  if (funds.length === 0) return null;
  return (
    <View style={[!first && s.groupDivider]}>
      <Text style={s.groupLabel}>{label}</Text>
      {funds.map((f) => {
        const provisional = parseFloat(f.provisional);
        const isLocked = !f.spendable;
        return (
          <PressScale
            key={f.id}
            scaleTo={0.985}
            disabled={!writable}
            onPress={() => router.push(`/spend?fund=${f.code}`)}
            style={s.row}
          >
            <View style={[s.swatch, { backgroundColor: fundColor(f) }]} />
            <View style={{ flex: 1 }}>
              <Text style={s.rowName}>{f.label_en}</Text>
              <Text style={s.rowRule}>{RULE_LINE[f.category] ?? f.constraint_note}</Text>
            </View>
            {isLocked && <Icon name="lock" size={15} color={colors.inkFaint} strokeWidth={1.9} />}
            <View style={{ alignItems: 'flex-end' }}>
              <Text style={s.rowAmount}>
                {parseFloat(f.balance).toLocaleString('en-MY', {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}
              </Text>
              {provisional > 0 && (
                <Text style={s.rowProvisional}>+ {provisional.toFixed(2)} provisional</Text>
              )}
            </View>
          </PressScale>
        );
      })}
    </View>
  );
}

function HomeSkeleton() {
  return (
    <View style={{ gap: 14, marginTop: 14 }}>
      <Skeleton height={176} radius={24} />
      <Skeleton height={68} radius={20} />
      <Skeleton height={16} width={80} />
      <Skeleton height={250} radius={20} />
    </View>
  );
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.paper },
  glow: {
    position: 'absolute',
    top: -150,
    left: -90,
    width: 420,
    height: 420,
    borderRadius: 210,
    backgroundColor: 'rgba(79,227,168,0.06)',
  },
  content: { padding: 20, paddingTop: 56, paddingBottom: 130, gap: 14 },

  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  headRight: { flexDirection: 'row', gap: 10 },
  headLeft: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  institution: { fontFamily: fonts.display, fontSize: 15, color: colors.ink },
  role: { fontFamily: fonts.body, fontSize: 12, color: colors.inkFaint, marginTop: 1 },
  iconBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    borderWidth: 1,
    borderColor: colors.lineStrong,
    backgroundColor: colors.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },

  bellDot: {
    position: 'absolute',
    top: 10,
    right: 11,
    width: 9,
    height: 9,
    borderRadius: 5,
    backgroundColor: colors.danger,
    borderWidth: 1.5,
    borderColor: colors.surface,
  },
  alertCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 12,
    paddingLeft: 14,
    borderRadius: radius.lg,
    backgroundColor: colors.brassSoft,
    borderWidth: 1,
    borderColor: 'rgba(240,198,116,0.35)',
  },
  alertCardCritical: {
    backgroundColor: colors.dangerSoft,
    borderColor: 'rgba(255,122,107,0.4)',
  },
  alertIcon: {
    width: 40,
    height: 40,
    borderRadius: 12,
    backgroundColor: 'rgba(6,16,13,0.4)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  alertTitle: { fontFamily: fonts.bodySemi, fontSize: 14, color: colors.ink },
  alertSub: { fontFamily: fonts.body, fontSize: 12, color: colors.inkSoft, marginTop: 2 },

  errorBar: { padding: space.md, backgroundColor: colors.dangerSoft, borderRadius: radius.md },
  errorText: { fontFamily: fonts.body, fontSize: 14, color: colors.danger },
  errorHint: { fontFamily: fonts.body, fontSize: 12, color: colors.danger, marginTop: 2, opacity: 0.8 },

  hero: {
    borderRadius: radius.xl,
    padding: 18,
    backgroundColor: colors.emerald,
    borderWidth: 1,
    borderColor: 'rgba(79,227,168,0.28)',
  },
  heroTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  heroLabel: { fontFamily: fonts.bodyBold, fontSize: 12, letterSpacing: 1.2, color: '#A9D8C5' },
  sealPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingVertical: 5,
    paddingHorizontal: 10,
    borderRadius: 999,
    backgroundColor: 'rgba(6,16,13,0.45)',
  },
  sealPillBroken: { backgroundColor: 'rgba(255,122,107,0.2)' },
  sealText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.green },
  heroAmount: {
    fontFamily: fonts.display,
    fontSize: 36,
    letterSpacing: -1,
    color: colors.ink,
    marginTop: 8,
  },
  bar: { flexDirection: 'row', gap: 3, marginTop: 14, overflow: 'hidden', borderRadius: 4 },
  heroFoot: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 12,
  },
  heroFootText: { fontFamily: fonts.body, fontSize: 12, color: '#A9D8C5' },
  pendingRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  pendingDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: colors.brass },
  pendingText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.brass },

  boxCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 12,
    paddingLeft: 14,
    borderRadius: radius.lg,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  boxIcon: {
    width: 40,
    height: 40,
    borderRadius: 12,
    backgroundColor: colors.brassSoft,
    alignItems: 'center',
    justifyContent: 'center',
  },
  boxTitle: { fontFamily: fonts.bodySemi, fontSize: 14, color: colors.ink },
  boxSub: { fontFamily: fonts.body, fontSize: 12, color: colors.inkFaint, marginTop: 2 },
  countBtn: {
    minWidth: 76,
    height: 44,
    borderRadius: 14,
    backgroundColor: colors.green,
    alignItems: 'center',
    justifyContent: 'center',
  },
  countBtnText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.onGreen },

  fundsHead: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between' },
  fundsTitle: { fontFamily: fonts.display, fontSize: 16, color: colors.ink },
  fundsSub: { fontFamily: fonts.body, fontSize: 12, color: colors.inkFaint },

  list: {
    paddingHorizontal: 14,
    paddingBottom: 4,
    borderRadius: radius.lg,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  groupDivider: { borderTopWidth: 1, borderTopColor: colors.line },
  groupLabel: {
    fontFamily: fonts.bodyBold,
    fontSize: 10.5,
    letterSpacing: 1.3,
    color: colors.inkFaint,
    paddingTop: 12,
    paddingBottom: 2,
  },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 54, paddingVertical: 6 },
  swatch: { width: 10, height: 10, borderRadius: 3 },
  rowName: { fontFamily: fonts.bodySemi, fontSize: 14.5, color: colors.ink },
  rowRule: { fontFamily: fonts.body, fontSize: 11.5, color: colors.inkFaint, marginTop: 1 },
  rowAmount: { fontFamily: fonts.display, fontSize: 14.5, color: colors.ink },
  rowProvisional: { fontFamily: fonts.body, fontSize: 11, color: colors.brass, marginTop: 1 },

  readOnly: {
    fontFamily: fonts.body,
    fontSize: 12.5,
    color: colors.inkFaint,
    textAlign: 'center',
    marginTop: 4,
  },
});
