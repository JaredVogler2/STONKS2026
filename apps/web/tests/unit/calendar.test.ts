import { describe, expect, it } from "vitest";
import { etParts, marketPhase, sessionsBetween } from "@/server/market/calendar";

/* Known XNYS facts (exchange_calendars export shared with the Pi). UTC instants are chosen around DST edges on purpose. */
const at = (iso: string) => new Date(iso);

describe("XNYS market phase", () => {
  it.each([
    ["2026-01-19T16:00:00Z", "CLOSED", "MLK Day"],
    ["2026-04-03T16:00:00Z", "CLOSED", "Good Friday"],
    ["2026-06-19T16:00:00Z", "CLOSED", "Juneteenth"],
    ["2026-07-03T16:00:00Z", "CLOSED", "Independence Day observed (July 4 is a Saturday)"],
    ["2026-11-26T16:00:00Z", "CLOSED", "Thanksgiving"],
    ["2026-12-25T16:00:00Z", "CLOSED", "Christmas"],
    ["2027-12-24T16:00:00Z", "CLOSED", "Christmas observed (Dec 25 2027 is a Saturday)"],
    ["2026-10-17T15:00:00Z", "CLOSED", "Saturday"],
  ])("%s is %s (%s)", (iso, phase) => {
    expect(marketPhase(at(iso)).phase).toBe(phase);
  });

  it("half sessions close at 13:00 ET and run after-hours until 17:00 ET", () => {
    const day = "2026-11-27"; // day after Thanksgiving
    expect(marketPhase(at(`${day}T17:59:00Z`))).toMatchObject({ phase: "OPEN", early_close: true });
    expect(marketPhase(at(`${day}T18:01:00Z`)).phase).toBe("AFTER");
    expect(marketPhase(at(`${day}T21:59:00Z`)).phase).toBe("AFTER");
    expect(marketPhase(at(`${day}T22:01:00Z`)).phase).toBe("CLOSED");
    expect(marketPhase(at("2026-12-24T17:30:00Z")).early_close).toBe(true);
  });

  it("follows the DST switch: open is 14:30Z in winter and 13:30Z in summer", () => {
    expect(marketPhase(at("2026-03-06T14:35:00Z")).phase).toBe("OPEN"); // Friday before the switch (EST)
    expect(marketPhase(at("2026-03-09T13:25:00Z")).phase).toBe("PRE"); // Monday after (EDT): 09:25 ET
    expect(marketPhase(at("2026-03-09T13:35:00Z")).phase).toBe("OPEN");
    expect(marketPhase(at("2026-11-02T13:35:00Z")).phase).toBe("PRE"); // back to EST: 08:35 ET
    expect(marketPhase(at("2026-11-02T14:35:00Z")).phase).toBe("OPEN");
  });

  it("covers pre-market from 04:00 ET and reports the next open when closed", () => {
    expect(marketPhase(at("2026-10-20T07:59:00Z")).phase).toBe("CLOSED"); // 03:59 ET
    expect(marketPhase(at("2026-10-20T08:01:00Z")).phase).toBe("PRE");
    const closed = marketPhase(at("2026-04-03T16:00:00Z"));
    expect(closed.next_open_utc).toBe("2026-04-06T13:30:00Z");
  });

  it("converts to ET dates across midnight UTC", () => {
    expect(etParts(at("2026-10-21T02:30:00Z"))).toEqual({ date: "2026-10-20", time: "22:30" });
    expect(etParts(at("2026-01-01T04:59:00Z")).date).toBe("2025-12-31");
  });

  it("counts trading sessions, skipping weekends and holidays", () => {
    expect(sessionsBetween("2026-04-01T20:00:00Z", "2026-04-07T20:00:00Z")).toBe(3); // Apr 2, (Good Friday closed), Apr 6, Apr 7
    expect(sessionsBetween("2026-11-25T20:00:00Z", "2026-11-30T20:00:00Z")).toBe(2); // Nov 27 (half), Nov 30
  });

  it("degrades honestly outside the exported range", () => {
    expect(marketPhase(at("2031-06-02T15:00:00Z"))).toMatchObject({ calendar_ok: false, label: "Calendar unavailable" });
  });
});
