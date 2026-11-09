-- How far the radar follows an organiser (ADR 0021). The seed owns it for every organiser it
-- names; any other organiser is a calendar, whose events need a topic. The updates give the
-- rows of 2026-11-08 their seed standing at once, so a job that judges the board before the
-- next seed run drops no followed organiser's event.

ALTER TABLE organiser ADD COLUMN standing text NOT NULL DEFAULT 'calendar'
    CHECK (standing IN ('anchor', 'community', 'calendar'));

UPDATE organiser SET standing = 'community'
WHERE discovered_via = 'seed'
    AND domain NOT IN ('tum.de', 'badw.de', 'lmu.de/ai-hub', 'munich-startup.de', 'baiosphere.org');

UPDATE organiser SET standing = 'anchor'
WHERE domain IN ('cdtm.com', 'tum-ai.com', 'startmunich.de', 'mcml.ai');
