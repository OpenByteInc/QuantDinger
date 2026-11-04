UPDATE qd_users
SET role = 'user', updated_at = NOW()
WHERE role = 'manager';
