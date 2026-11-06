-- Social Markhors Bot - Database Schema
-- Alag tables Facebook aur Instagram ke liye

create table if not exists fb_accounts (
  id bigserial primary key,
  username text unique,
  fb_id text unique,
  access_token text,
  cookies jsonb not null,
  country text,
  followers int default 0,
  verified boolean default false,
  status text default 'live',
  fails int default 0,
  source text,
  created_at timestamptz default now(),
  last_check timestamptz,
  last_ok timestamptz,
  last_post timestamptz
);
create index if not exists fb_accounts_status_idx on fb_accounts (status);
create index if not exists fb_accounts_username_idx on fb_accounts (username);

create table if not exists ig_accounts (
  id bigserial primary key,
  username text unique,
  ig_id text unique,
  access_token text,
  cookies jsonb not null,
  country text,
  followers int default 0,
  verified boolean default false,
  status text default 'live',
  fails int default 0,
  source text,
  created_at timestamptz default now(),
  last_check timestamptz,
  last_ok timestamptz,
  last_post timestamptz
);
create index if not exists ig_accounts_status_idx on ig_accounts (status);
create index if not exists ig_accounts_username_idx on ig_accounts (username);

-- RLS enable (service_role bypasses, but good practice)
alter table fb_accounts enable row level security;
alter table ig_accounts enable row level security;
