-- An event intake is given by its URL alone; the check reads the day and place from the page
-- (ADR 0019). The columns stay for the intakes checked against a given day and place.

ALTER TABLE intake DROP CONSTRAINT intake_event_names_day_and_place;
