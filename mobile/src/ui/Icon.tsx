import Svg, { Circle, Path, Rect } from 'react-native-svg';

export type IconName =
  | 'home'
  | 'spend'
  | 'count'
  | 'ledger'
  | 'lock'
  | 'shield'
  | 'mail'
  | 'box'
  | 'logout'
  | 'bell'
  | 'alert'
  | 'camera'
  | 'chevron'
  | 'check'
  | 'x'
  | 'pulse';

/** Stroke icons drawn on a 24 grid, same family as the design mockups. */
export function Icon({
  name,
  size = 22,
  color = '#ECF5F1',
  strokeWidth = 1.8,
}: {
  name: IconName;
  size?: number;
  color?: string;
  strokeWidth?: number;
}) {
  const p = {
    fill: 'none',
    stroke: color,
    strokeWidth,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24">
      {name === 'home' && <Path {...p} d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" />}
      {name === 'spend' && (
        <>
          <Path {...p} d="M7 17L17 7" />
          <Path {...p} d="M9 7h8v8" />
        </>
      )}
      {name === 'count' && (
        <>
          <Circle {...p} cx={12} cy={12} r={9} />
          <Path {...p} d="M8 12.5l3 3 5-6" />
        </>
      )}
      {name === 'ledger' && (
        <>
          <Path {...p} d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z" />
          <Path {...p} d="M5 17a3 3 0 0 1 3-3h11" />
        </>
      )}
      {name === 'lock' && (
        <>
          <Rect {...p} x={5} y={11} width={14} height={9} rx={2} />
          <Path {...p} d="M8 11V8a4 4 0 0 1 8 0v3" />
        </>
      )}
      {name === 'shield' && (
        <>
          <Path {...p} d="M12 3l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V6z" />
          <Path {...p} d="M9 12l2.2 2.2L15.5 10" />
        </>
      )}
      {name === 'mail' && (
        <>
          <Rect {...p} x={3} y={5} width={18} height={14} rx={3} />
          <Path {...p} d="M4 7l8 6 8-6" />
        </>
      )}
      {name === 'box' && (
        <>
          <Path {...p} d="M4 14h16l-1.5 6h-13z" />
          <Path {...p} d="M12 3v7" />
          <Path {...p} d="M9 7l3 3 3-3" />
        </>
      )}
      {name === 'logout' && (
        <>
          <Path {...p} d="M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4" />
          <Path {...p} d="M15 8l4 4-4 4" />
          <Path {...p} d="M19 12H9" />
        </>
      )}
      {name === 'bell' && (
        <>
          <Path {...p} d="M6 9a6 6 0 0 1 12 0c0 6 2 7 2 7H4s2-1 2-7z" />
          <Path {...p} d="M10 20a2 2 0 0 0 4 0" />
        </>
      )}
      {name === 'alert' && (
        <>
          <Path {...p} d="M12 4l9 16H3z" />
          <Path {...p} d="M12 10v4" />
          <Path {...p} d="M12 17.4h.01" />
        </>
      )}
      {name === 'camera' && (
        <>
          <Path
            {...p}
            d="M4 8h3l1.5-2h7L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"
          />
          <Circle {...p} cx={12} cy={13} r={3.5} />
        </>
      )}
      {name === 'chevron' && <Path {...p} d="M9 6l6 6-6 6" />}
      {name === 'check' && <Path {...p} d="M5 12.5l4.5 4.5L19 7.5" />}
      {name === 'x' && <Path {...p} d="M6 6l12 12M18 6L6 18" />}
      {name === 'pulse' && <Path {...p} d="M3 12h4l2.5-6 4 12 2.5-6H21" />}
    </Svg>
  );
}
