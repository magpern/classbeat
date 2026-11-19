import type { Revision } from "../../api/classes";
import type { Song } from "../../api/songs";
import { useT } from "../../i18n/I18n";
import { zoneName, type Bar } from "../../lib/profile";
import { clock, totalSeconds, tss } from "../../lib/workout";
import { StatTile, StatTiles } from "../ui/stat-tile";
import { TssValue } from "./TssValue";
import { tssApproximateNote } from "../../lib/classCards";

/** Length, TSS, segments, songs and hardest zone of a version, as Afterburn stat tiles. */
export function RevisionStats({
  revision,
  bars,
  running,
  songs,
}: {
  revision: Revision;
  bars: Bar[];
  running: boolean;
  songs: Song[] | null;
}) {
  const t = useT();
  const hardest =
    bars.length === 0 ? null : Math.max(...bars.map((b) => b.zone));
  return (
    <StatTiles
      aria-label={t("class.revision.stats")}
      className="lg:grid-cols-5"
    >
      <StatTile
        label={t("class.revision.length")}
        value={
          <span data-testid="duration">
            {clock(totalSeconds(revision.segments))}
          </span>
        }
      />
      {running ? null : (
        <StatTile
          label={t("class.revision.tss")}
          value={
            <span data-testid="tss">
              <TssValue
                value={tss(revision.segments)}
                approximate={revision.tssApproximate}
              />
            </span>
          }
          sub={
            revision.tssApproximate ? (
              <span data-testid="tss-note">({tssApproximateNote()})</span>
            ) : undefined
          }
        />
      )}
      <StatTile
        label={t("class.revision.segments")}
        value={revision.segments.length}
      />
      {songs !== null && songs.length > 0 ? (
        <StatTile label={t("class.revision.songs")} value={songs.length} />
      ) : null}
      {hardest !== null ? (
        <StatTile
          label={t("class.revision.hardest")}
          value={t("class.zone.short", { zone: hardest })}
          sub={zoneName(hardest)}
        />
      ) : null}
    </StatTiles>
  );
}
