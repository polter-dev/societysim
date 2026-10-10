/** Unstyled. "● Live", or "● Live, N new" once the reader has stepped back. Class: live-badge. */
export function LiveBadge({ newCount }: { newCount: number }) {
  return (
    <span className="live-badge" role="status">
      ● Live{newCount > 0 && `, ${newCount} new`}
    </span>
  );
}
