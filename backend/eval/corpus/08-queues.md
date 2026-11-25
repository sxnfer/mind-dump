# Work queues and delivery guarantees

A work queue holds tasks until a worker is free to do them. It lets the part of a system that accepts requests answer quickly while slow work, such as resizing images or calling an external API, happens in the background at whatever pace the workers can manage.

Queues promise one of three delivery guarantees. At-most-once delivery never repeats a task but can lose one when a worker crashes. At-least-once delivery never loses a task but can deliver it twice, so the work must be idempotent. Exactly-once delivery is what everyone wants, and in practice it is built from at-least-once delivery plus deduplication.

A visibility timeout, also called a lease, is how a queue notices a dead worker. When a worker takes a task, the task becomes invisible to other workers for a fixed time. If the worker finishes, it deletes the task; if it crashes, the timeout runs out and the task becomes visible again for someone else.

Tasks that fail every time need a way out, or they will block the queue forever. After a set number of attempts, the queue moves the task to a dead-letter queue, where a person can inspect the error, fix the cause and send it back. Postgres can serve as a queue for moderate loads by claiming rows with SELECT ... FOR UPDATE SKIP LOCKED, which lets many workers take different rows without waiting for each other.

Backoff spaces out retries. Instead of retrying a failed call at once, the worker waits a little, then longer after each failure, so that a struggling service gets time to recover instead of a flood of repeated requests.

Ordering is a weaker promise than most people expect. Even a queue that hands out tasks in order cannot keep them in order when several workers run at once, because a fast worker can finish the second task before a slow worker finishes the first.
