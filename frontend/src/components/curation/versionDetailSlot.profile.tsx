// Version detail slot: the profile (FR-004.43).
import { useEffect } from 'react';

import { Button } from '@/components/common/Button';
import type { VersionSlot } from '@/components/versions/versionSlots';
import { useCurationStore } from '@/stores/curationStore';
import type { Version } from '@/types/versions';

import { ProfileHistograms } from './ProfileHistograms';

export function ProfileSlot({ version }: { version: Version }) {
  const profile = useCurationStore((s) => s.profilesByVersion[version.id]);
  const running = useCurationStore((s) => s.running.profile ?? false);
  const error = useCurationStore((s) => s.errors.profile ?? null);
  const fetchProfile = useCurationStore((s) => s.fetchProfile);
  const runProfile = useCurationStore((s) => s.runProfile);
  useEffect(() => {
    void fetchProfile(version.id);
  }, [version.id, fetchProfile]);
  if (profile) return <ProfileHistograms profile={profile} />;
  return (
    <div className="text-sm">
      {profile === undefined && !error && <p className="text-slate-500 dark:text-slate-400">Loading the profile…</p>}
      {profile === null && <p className="text-slate-500 dark:text-slate-400">No profile yet.</p>}
      <Button size="sm" className="mt-2" loading={running} onClick={() => void runProfile(version.id)}>
        Profile this version
      </Button>
      {error && <p role="alert" className="text-xs text-red-700 dark:text-red-300 mt-1">{error}</p>}
    </div>
  );
}

const slot: VersionSlot = { id: 'profile', order: 120, title: 'Profile', applies: () => true, Component: ProfileSlot };
export default slot;
