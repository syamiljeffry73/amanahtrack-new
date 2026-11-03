import { usePushSetup } from '../push';
import { useSession } from '../session';

/** Renders nothing. Sets up push notifications for the signed in user. */
export function PushSetup() {
  const { session } = useSession();
  usePushSetup(!!session);
  return null;
}
