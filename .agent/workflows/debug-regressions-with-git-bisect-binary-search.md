---
description: find the commit that introduced a bug
---

---
description: Automatically find the exact commit that introduced a bug
---

1. **Start Bisect**:
   - Initialize the bisect process.
   // turbo
   - Run `git bisect start`

2. **Mark Current Commit as Bad**:
   - Tell Git that the current version is broken.
   // turbo
   - Run `git bisect bad`

3. **Mark an Old Commit as Good**:
   - Find a commit hash from when the feature was definitely working and mark it as good. Replace `[good-commit-hash]` with the actual hash.
   // turbo
   - Run `git bisect good [good-commit-hash]`

4. **Test and Repeat**:
   - Git will checkout a commit in the middle. Test your app.
   - If it works: Run `git bisect good`
   - If it's broken: Run `git bisect bad`
   - Repeat until Git tells you: `[hash] is the first bad commit`.

5. **Finish**:
   - Reset the bisect state to return to the original branch.
   // turbo
   - Run `git bisect reset`

6. **Pro Tips**:
   - You can automate this! Run `git bisect run npm test` to let a test script do the work for you.