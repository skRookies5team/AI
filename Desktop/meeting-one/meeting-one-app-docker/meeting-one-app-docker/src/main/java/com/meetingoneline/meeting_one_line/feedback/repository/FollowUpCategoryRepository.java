package com.meetingoneline.meeting_one_line.feedback.repository;

import com.meetingoneline.meeting_one_line.feedback.entity.*;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.UUID;

public interface FollowUpCategoryRepository extends JpaRepository<FollowUpCategoryEntity, UUID> { }

