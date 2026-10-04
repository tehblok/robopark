# Production PWA fixture and offline media preservation

CI run 37215819846 passed 47 production PWA cases and failed one WebKit
case: reading a seeded photo after a native IndexedDB state overwrite raised
NotFoundError. Checkpoints reproduced loss immediately after the fixture write,
before the service-worker activation or reload. The original seeded bytes were
readable. The backing file still existed and the database mapped it, so file
deletion is not an established cause. Moving the database close or splitting
transactions while reusing the persisted Blob did not resolve it. Minimal
native IndexedDB controls did not reproduce the full fixture's failure.

The fixture now materializes the existing bytes into a fresh Blob before its
state-only setup write. Twenty repeated full WebKit activation workflows passed.
The final assertions still read and compare exact persisted photo bytes after
activation; no production storage logic or browser tolerance changed. This is a
fixture workaround, not a proven upstream root-cause diagnosis or a claim that
all browser storage failures are resolved.

Separate browser regressions use the real OfflineDb module, persist a photo,
close/reopen its database, acquire the lease and rewrite only metadata. Both
20 immediate writes and two writes holding the file-backed Blob across a
1.1-second boundary must preserve exact bytes before and after another reload.
Three repeats of each mode in Chromium, Firefox and WebKit passed: 18/18.

WebKit's FileStream checks the expected modification time as well as existence,
which is one compatible explanation for an existing file becoming unreadable.
It remains a hypothesis: source inspection was not a proof for the exact bundled
engine. The complete production PWA and cross-browser gates remain required.

Sources: [FileStream](https://github.com/WebKit/WebKit/blob/main/Source/WebCore/platform/FileStream.cpp),
[BlobDataFileReference](https://github.com/WebKit/WebKit/blob/main/Source/WebCore/platform/network/BlobDataFileReference.cpp),
[IndexedDB backing store](https://github.com/WebKit/WebKit/blob/main/Source/WebCore/Modules/indexeddb/server/SQLiteIDBBackingStore.cpp).

Final full production PWA run: 48/48 passed across Chromium, Firefox and WebKit in 3.6 minutes. This includes exact durable-media preservation and both production document CSPs.
