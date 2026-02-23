# 🦜 Canary Log
**Last updated:** 2026-02-22T14:30:00Z
**Base branch:** `main`
**Active branches:** 2

> **For Claude Code sessions:** Check this log before modifying any
> shared file. If your planned change conflicts with an entry below,
> coordinate before proceeding.

---

## 🔒 Active Locks

### `src/types/user.ts`
**Claimed by:** `feature-api-v2` (backend-dev)
**Reason:** Restructuring User type — splitting name field, adding phone verification
**Expires:** 2026-02-22T16:30:00Z
→ Wait before building against this file, or coordinate with the owner.

---

## ⚠️ Direct File Conflicts

These files are modified by multiple branches simultaneously.

### 🐦‍🔥 `src/types/user.ts`
**Severity:** high | **Category:** Shared Types
**Branches:** `feature-api-v2`, `feature-frontend-redesign`

**What changed:** Renamed: `email` → `emailAddress`, `name` → `firstName`; Added: `lastName`, `phoneNumber`, `phoneVerified`
- **feature-frontend-redesign:** These files import from `src/types/user.ts` and may break
  - `src/components/UserProfile.tsx`
  - `src/components/SignupForm.tsx`
  - `src/hooks/useUser.ts`
**Interfaces:** `email` → `emailAddress`, `name` → `firstName`

### 🐦‍🔥 `src/api/routes/users.ts`
**Severity:** high | **Category:** Api Contracts
**Branches:** `feature-api-v2`, `feature-frontend-redesign`

**What changed:** Modified: response body shape changed to match new User type; Added: `verifyPhone` endpoint
- **feature-frontend-redesign:** These files import from `src/api/routes/users.ts` and may break
  - `src/utils/apiClient.ts`

### 🐦 `src/config/endpoints.ts`
**Severity:** medium | **Category:** Config
**Branches:** `feature-api-v2`, `feature-frontend-redesign`

**What changed:** Added: `VERIFY_PHONE` endpoint path

---

## 🔗 Dependency Conflicts

These files aren't directly overlapping, but are connected via imports.

### `src/types/user.ts` (Shared Types)
**Changed on:** `feature-api-v2`
**Affects on `feature-frontend-redesign`:**
- `src/components/UserProfile.tsx` (imports from `src/types/user.ts`)
- `src/components/SignupForm.tsx` (imports from `src/types/user.ts`)
- `src/hooks/useUser.ts` (imports from `src/types/user.ts`)
- `src/pages/ProfilePage.tsx` (imports from `src/components/UserProfile.tsx` → `src/types/user.ts`)

### `src/api/routes/users.ts` (Api Contracts)
**Changed on:** `feature-api-v2`
**Affects on `feature-frontend-redesign`:**
- `src/utils/apiClient.ts` (imports from `src/api/routes/users.ts`)

---

## 🧪 Merge Dry-Run Results

- ❌ `feature-api-v2` ↔ `feature-frontend-redesign`: **Conflicts** in:
  - `src/types/user.ts`
  - `src/api/routes/users.ts`
  - `src/config/endpoints.ts`

---

## Branch Change Summaries

### `feature-api-v2`
**Files changed:** 12

**Recent commits:**
- a3f21c8 refactor: rename email to emailAddress across user schema
- b7e09d4 feat: add phone verification endpoint
- c1a88f2 fix: update JWT payload to include user role
- d9e33a1 chore: migrate users table, add phone_verified column

**⚡ High-impact changes:**
- [Api Contracts] `src/api/routes/users.ts` — Modified
- [Api Contracts] `src/api/routes/auth.ts` — Modified
- [Shared Types] `src/types/user.ts` — Modified
- [Shared Types] `src/types/auth.ts` — Modified
- [Database] `prisma/migrations/20260222_add_phone_verified.sql` — Added
- [Database] `prisma/schema.prisma` — Modified
- [Config] `src/config/endpoints.ts` — Modified

**Other changes (5 files):**
- `src/api/middleware/validate.ts` — Modified
- `src/api/services/userService.ts` — Modified
- `src/api/services/authService.ts` — Modified
- `tests/api/users.test.ts` — Modified
- `tests/api/auth.test.ts` — Modified

---

### `feature-frontend-redesign`
**Files changed:** 18

**Recent commits:**
- e5f12b3 feat: new user profile page with avatar upload
- f8a44c1 feat: redesign signup form with multi-step flow
- g2b77d9 fix: handle loading states on user data fetch
- h6c99e0 chore: add react-query for API state management

**⚡ High-impact changes:**
- [Api Contracts] `src/api/routes/users.ts` — Modified
- [Shared Types] `src/types/user.ts` — Modified
- [Config] `src/config/endpoints.ts` — Modified
- [Shared Utilities] `src/utils/apiClient.ts` — Modified

**Other changes (14 files):**
- `src/components/UserProfile.tsx` — Modified
- `src/components/SignupForm.tsx` — Modified
- `src/components/AvatarUpload.tsx` — Added
- `src/hooks/useUser.ts` — Added
- `src/hooks/useAuth.ts` — Modified
- `src/pages/ProfilePage.tsx` — Modified
- `src/pages/SignupPage.tsx` — Modified
- `src/styles/profile.css` — Added
- `src/styles/signup.css` — Modified
- `tests/components/UserProfile.test.tsx` — Modified
- ... and 4 more

---

## Recommended Actions

**Immediate:**
- Coordinate changes to `src/types/user.ts` between `feature-api-v2` and `feature-frontend-redesign`
- Coordinate changes to `src/api/routes/users.ts` between `feature-api-v2` and `feature-frontend-redesign`

**General:**
- Merge the branch with fewer changes first, then rebase the other
- Agree on the final shape of shared interfaces before continuing
- Use file locks to claim critical files before making breaking changes
