-- The app has one workflow from here on. A project created under the first workflow continues as a V4.5
-- project; its old shots, approvals and receipts stay in their tables as history and count for nothing in
-- the new checkpoints. The database as it was is kept in data/archive/ before this runs.
UPDATE projects SET workflow_version='V4_5' WHERE workflow_version!='V4_5';
