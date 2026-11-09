-- Anonymous visitors never need the role lookups (they returned false/empty anyway).
revoke execute on function public.has_role(text), public.my_roles() from anon;
