package com.meetingoneline.meeting_one_line.meeting.repository;

import com.meetingoneline.meeting_one_line.meeting.entity.MeetingEntity;
import com.meetingoneline.meeting_one_line.meeting.enums.RecordSaveStatus;
import com.meetingoneline.meeting_one_line.user.UserEntity;
import org.springframework.data.jpa.domain.Specification;

public class MeetingSpecification {

    public static Specification<MeetingEntity> byUser(UserEntity user) {
        return (root, query, cb) -> cb.equal(root.get("user"), user);
    }

    public static Specification<MeetingEntity> titleOrSummaryContains(String keyword) {
        return (root, query, cb) -> cb.or(
                cb.like(cb.lower(root.get("title")), "%" + keyword.toLowerCase() + "%"),
                cb.like(cb.lower(root.get("summary")), "%" + keyword.toLowerCase() + "%")
        );
    }

    public static Specification<MeetingEntity> titleContains(String title) {
        return (root, query, cb) -> cb.like(cb.lower(root.get("title")), "%" + title.toLowerCase() + "%");
    }

    public static Specification<MeetingEntity> summaryContains(String summary) {
        return (root, query, cb) -> cb.like(cb.lower(root.get("summary")), "%" + summary.toLowerCase() + "%");
    }

    public static Specification<MeetingEntity> hasStatus(RecordSaveStatus status) {
        return (root, query, cb) -> cb.equal(root.get("status"), status);
    }
}
