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


if __name__ == "__main__":
    unittest.main()
