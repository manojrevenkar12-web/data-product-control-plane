// Motion settings in one place, so every animation shares the same timing and respects reduced motion.
export const motion = {
  stampStagger: 0.035,
  stampDuration: 0.28,
  expandDuration: 0.22,
  ease: [0.2, 0.8, 0.2, 1] as [number, number, number, number],
};

export const methods = ["tag", "role", "user", "warehouse", "idle", "none"] as const;

export const methodLabel: Record<(typeof methods)[number], string> = {
  tag: "query tag",
  role: "role",
  user: "user's team",
  warehouse: "team warehouse",
  idle: "idle time",
  none: "unattributed",
};

// Each skeleton line is this many percent shorter than the one above, so the placeholder reads as text.
export const skeletonStep = 9;
