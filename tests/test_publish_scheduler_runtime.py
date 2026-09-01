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


if __name__ == "__main__":
    unittest.main()
