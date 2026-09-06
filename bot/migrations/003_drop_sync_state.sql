-- The git sync is gone with the member files. It rendered database rows back
-- into content/members/ and committed them; there are no such files now, and
-- the website reads the database through the API instead, so there is nothing
-- for a "changes are waiting" flag to mean.
drop table if exists sync_state;
