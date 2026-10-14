/** The hero story's fixed actors (design-system README: "the hero story"). */
export const STORY = { ifsc: "HDFC0001234", account: "ACC-999", amountMinor: 500_000 } as const;

/** Policy defaults from aris/schema.py PolicyConfig; every audit record
 * carries the thresholds actually in force (checked in the e2e suite). */
export const POLICY = { stepUpAt: 50, blockAt: 85 } as const;
