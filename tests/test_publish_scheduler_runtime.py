import unittest

from apscheduler.schedulers.base import STATE_STOPPED

from services.publish_scheduler_runtime import create_publish_scheduler


class PublishSchedulerRuntimeTests(unittest.TestCase):
    def test_builds_one_coalescing_single_instance_job_without_starting(self):
        scheduler = create_publish_scheduler("database.db", interval_seconds=15)

        jobs = scheduler.get_jobs()

        self.assertEqual(scheduler.state, STATE_STOPPED)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].id, "publish-jobs-due-tick")
        self.assertEqual(jobs[0].max_instances, 1)
        self.assertTrue(jobs[0].coalesce)
        self.assertEqual(jobs[0].kwargs, {"database_path": "database.db"})

    def test_rejects_too_frequent_polling(self):
        with self.assertRaisesRegex(ValueError, "5 秒"):
            create_publish_scheduler("database.db", interval_seconds=1)

    def test_passes_explicit_real_execution_dependencies_to_tick(self):
        factory = object()
        scheduler = create_publish_scheduler(
            "database.db",
            publisher_factory=factory,
            allow_real=True,
            media_root="videoFile",
        )

        kwargs = scheduler.get_jobs()[0].kwargs

        self.assertEqual(kwargs["database_path"], "database.db")
        self.assertIs(kwargs["publisher_factory"], factory)
        self.assertTrue(kwargs["allow_real"])
        self.assertEqual(kwargs["media_root"], "videoFile")

    def test_adds_separate_bounded_read_only_review_monitor(self):
        verifier = object()
        scheduler = create_publish_scheduler(
            "database.db",
            review_verifier=verifier,
            review_interval_seconds=60,
            review_limit=2,
        )

        jobs = {job.id: job for job in scheduler.get_jobs()}
        self.assertEqual(
            set(jobs),
            {"publish-jobs-due-tick", "publish-review-monitor-tick"},
        )
        review = jobs["publish-review-monitor-tick"]
        self.assertIs(review.kwargs["verifier"], verifier)
        self.assertEqual(review.kwargs["limit"], 2)
        self.assertEqual(review.max_instances, 1)
        self.assertTrue(review.coalesce)

    def test_rejects_high_frequency_review_monitoring(self):
        with self.assertRaisesRegex(ValueError, "30 秒"):
            create_publish_scheduler(
                "database.db",
                review_verifier=object(),
                review_interval_seconds=10,
            )


if __name__ == "__main__":
    unittest.main()
